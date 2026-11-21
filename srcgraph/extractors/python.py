"""Python extractor: tree-sitter AST -> shared vocabulary."""
import posixpath
import re
from dataclasses import replace

import tree_sitter_python
from tree_sitter import Language, Node, Parser

from srcgraph.extractors.lines import LineIndex
from srcgraph.extractors.syntax import ast_id, emit_syntax, text as _text
from srcgraph.model import Edge, Extraction, RawImport, RawRef, Symbol, Unit, is_test_path, symbol_id

LANG = "python"
_PARSER = Parser(Language(tree_sitter_python.language()))
PY_NORM = {
    "class_definition": "ClassDef", "function_definition": "FunctionDef", "call": "Call",
    "assignment": "Assign", "augmented_assignment": "Assign",
    "import_statement": "Import", "import_from_statement": "Import", "future_import_statement": "Import",
    "decorator": "Decorator", "attribute": "Attribute", "identifier": "Name", "string": "String",
    "return_statement": "Return", "if_statement": "If", "for_statement": "Loop", "while_statement": "Loop",
    "try_statement": "Try", "lambda": "Lambda",
    "default_parameter": "Parameter", "typed_parameter": "Parameter", "typed_default_parameter": "Parameter",
    "list_splat_pattern": "Parameter", "dictionary_splat_pattern": "Parameter",
    "keyword_argument": "Argument", "block": "Block", "comment": "Comment", "type": "Type",
    "integer": "Literal", "float": "Literal", "true": "Literal", "false": "Literal", "none": "Literal",
}


def _dotted(node: Node) -> str | None:
    """Dotted text of an identifier or attribute chain over identifiers; None for anything else."""
    if node.type == "identifier":
        return _text(node)
    if node.type == "attribute":
        head = _dotted(node.child_by_field_name("object"))
        attr = node.child_by_field_name("attribute")
        return f"{head}.{_text(attr)}" if head and attr is not None else None
    return None


WRAPPERS = {"Optional", "Iterator", "Generator", "Iterable", "AsyncIterator", "AsyncGenerator"}


def _type_name(node: Node | None) -> str | None:
    """The one class an annotation names, as written: `A`, `"A"`, `A | None`, `Optional[A]`, `Iterator[A]`. None when
    it names several or none: a missing edge is preferred to a guessed one."""
    if node is None:
        return None
    t = node.type
    if t in ("identifier", "attribute"):
        return _dotted(node)
    if t == "string":
        content = next((c for c in node.children if c.type == "string_content"), None)
        text = _text(content).strip() if content is not None else ""
        return text if re.fullmatch(r"[A-Za-z_][\w.]*", text) else None
    if t in ("union_type", "binary_operator"):
        names = [_type_name(c) for c in node.named_children if c.text != b"None"]
        return names[0] if len(names) == 1 else None
    if t in ("generic_type", "subscript"):
        head, *inner = node.named_children
        if (_dotted(head) or "").rsplit(".", 1)[-1] not in WRAPPERS or not inner:
            return None
        while inner[0].type in ("type_parameter", "type") and inner[0].named_children:
            inner = inner[0].named_children
        return _type_name(inner[0])
    return _type_name(node.named_children[0]) if t == "type" and node.named_children else None


def _doc(body: Node | None) -> str:
    """First paragraph of the docstring opening `body` (a module or a block), whitespace collapsed; "" if none."""
    first = next((c for c in body.named_children if c.type != "comment"), None) if body is not None else None
    if first is None or first.type != "expression_statement" or not first.named_children or first.named_children[0].type != "string":
        return ""
    content = next((c for c in first.named_children[0].children if c.type == "string_content"), None)
    text = _text(content).strip() if content is not None else ""
    return " ".join(re.split(r"\n\s*\n", text, maxsplit=1)[0].split())


def _imports(node: Node, unit_id: str, lines: LineIndex) -> list[RawImport]:
    line = lines.line(node.start_byte)
    if node.type == "import_statement":
        out = []
        for target in node.named_children:
            if target.type == "aliased_import":
                out.append(RawImport(unit_id, _text(target.child_by_field_name("name")), 0, line,
                                     alias=_text(target.child_by_field_name("alias"))))
            elif target.type == "dotted_name":
                out.append(RawImport(unit_id, _text(target), 0, line))
        return out
    # import_from_statement: from <module> import <names>
    module_node = node.child_by_field_name("module_name")
    level = 0
    module = "__future__" if node.type == "future_import_statement" else ""
    if module_node is not None:
        if module_node.type == "relative_import":
            for c in module_node.children:
                if c.type == "import_prefix":
                    level = len(_text(c))
                elif c.type == "dotted_name":
                    module = _text(c)
        else:
            module = _text(module_node)
    names = []
    for c in node.children_by_field_name("name"):
        if c.type == "aliased_import":
            names.append((_text(c.child_by_field_name("name")), _text(c.child_by_field_name("alias"))))
        else:
            names.append((_text(c), _text(c)))
    if any(c.type == "wildcard_import" for c in node.children):
        names.append(("*", "*"))
    return [RawImport(unit_id, module, level, line, names=tuple(names))]


def extract(rel_path: str, source: bytes, module_name: str) -> Extraction:
    ex = Extraction()
    kind = "package" if posixpath.basename(rel_path) == "__init__.py" else "module"
    tree = _PARSER.parse(source)
    ex.units.append(Unit(rel_path, rel_path, module_name, LANG, kind, doc=_doc(tree.root_node), test=is_test_path(rel_path)))
    lines = LineIndex(source)
    ex.ast, ex.ast_edges = emit_syntax(tree.root_node, PY_NORM, rel_path, LANG, lines)
    ex.edges.append(Edge("DERIVED_FROM", rel_path, ast_id(rel_path, tree.root_node)))
    classes: list[str] = []                       # qualified names of the classes being walked, innermost last
    self_calls: list[tuple[str, str, int]] = []   # (owner, candidate method qualified, line), checked once all methods are known
    scopes: list[set[str]] = []                   # parameter and local names of the functions being walked, innermost last
    types: list[dict[str, tuple[str, str]]] = []  # what each of those names is known to be, where the code says so
    is_test = is_test_path(rel_path)
    in_main = False                               # walking the body of a module-level `if __name__ == "__main__"`

    def is_local(name: str) -> bool:
        return bool(scopes) and name.partition(".")[0] in scopes[-1]

    def params(node: Node) -> set[str]:
        p = node.child_by_field_name("parameters")
        ids = [next((x for x in c.children if x.type == "identifier"), c) for c in (p.named_children if p is not None else [])]
        return {_text(i) for i in ids if i.type == "identifier"}

    def param_types(node: Node, inject: bool) -> dict[str, tuple[str, str]]:
        """Annotated parameters by their annotation; in a pytest test or fixture, the rest by the fixture they name."""
        out: dict[str, tuple[str, str]] = {}
        p = node.child_by_field_name("parameters")
        for c in (p.named_children if p is not None else []):
            if c.type in ("typed_parameter", "typed_default_parameter"):
                ident = c.child_by_field_name("name") or next((x for x in c.children if x.type == "identifier"), None)
                name = _type_name(c.child_by_field_name("type"))
                if ident is not None and ident.type == "identifier" and name:
                    out[_text(ident)] = ("type", name)
            elif inject and c.type == "identifier" and _text(c) not in ("self", "cls"):
                out[_text(c)] = ("fixture", _text(c))
        return out

    def value_of(node: Node | None) -> tuple[str, str] | None:
        """What an expression gives back: a local already known, or the result of calling a name."""
        if node is None:
            return None
        if node.type == "identifier":
            return types[-1].get(_text(node)) if types else None
        if node.type == "attribute":   # self.attr
            head, _, attr = (_dotted(node) or "").partition(".")
            return ("attr", (classes[-1], attr)) if head in ("self", "cls") and classes and attr and "." not in attr else None
        if node.type == "call":
            name = _dotted(node.child_by_field_name("function"))
            head, _, method = (name or "").partition(".")
            if name and method and "." not in method and types and head in types[-1]:
                return ("result", (types[-1][head], method))   # what a method on a known object gives back
            return ("call", name) if name and not is_local(name) else None
        return None

    def symbol(node: Node, qualified: str, kind: str, doc: str = "") -> Symbol:
        name = qualified.rsplit(".", 1)[-1]
        sym = Symbol(symbol_id(rel_path, qualified), name, qualified, kind, LANG, rel_path,
                     lines.line(node.start_byte), lines.line(max(node.end_byte - 1, node.start_byte)), node.type, doc)
        ex.edges.append(Edge("DERIVED_FROM", sym.id, ast_id(rel_path, node)))
        return sym

    GUARDS = {"if_statement": "if", "for_statement": "for", "while_statement": "while", "try_statement": "try"}
    guards: list[str] = []

    def define(node: Node, scope: str, owner: str, in_class: bool, fixture: bool = False) -> str | None:
        """Create the symbol for a function/class definition node, walk its body, return the symbol id."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        qualified = f"{scope}.{_text(name_node)}"
        doc = _doc(node.child_by_field_name("body"))
        if node.type == "class_definition":
            sym = symbol(node, qualified, "class", doc)
            bases = node.child_by_field_name("superclasses")
            for base in (bases.children if bases is not None else []):
                if not base.is_named or base.type in ("keyword_argument", "comment", "list_splat", "dictionary_splat"):
                    continue  # metaclass=..., frozen=True, comments and *args are not bases
                if base.type == "subscript":  # Generic[T] -> Generic
                    base = base.child_by_field_name("value") or base
                ex.refs.append(RawRef(sym.id, _dotted(base) or _text(base), lines.line(base.start_byte), "inherits"))
        else:
            sym = symbol(node, qualified, "method" if in_class else "function", doc)
        ex.symbols.append(sym)
        ex.edges.append(Edge("CONTAINS", owner, sym.id))
        body = node.child_by_field_name("body")
        if body is not None:
            is_class = node.type == "class_definition"
            stack = classes if is_class else scopes
            stack.append(qualified if is_class else params(node))
            if not is_class:
                known = param_types(node, is_test and (fixture or _text(name_node).startswith("test")))
                if in_class and classes:
                    known.update({me: ("class", classes[-1]) for me in ("self", "cls")})
                types.append(known)
                for pname, (how, what) in known.items():   # pytest calls each fixture on the test's behalf
                    if how == "fixture":
                        ex.refs.append(RawRef(sym.id, "", sym.line, "calls", receiver=("fixture", what)))
                declared = _type_name(node.child_by_field_name("return_type"))
                if declared:
                    ex.returns[sym.id] = ("type", declared)
            guards.append("")
            for field in ("parameters", "return_type"):   # annotations and defaults name things; parameter names are local
                part = node.child_by_field_name(field)
                if part is not None and not is_class:
                    walk(part, qualified, sym.id, is_class, not is_class)
            walk(body, qualified, sym.id, is_class, not is_class)
            guards.pop()
            stack.pop()
            if not is_class:
                types.pop()
        return sym.id

    def walk(node: Node, scope: str, owner: str, in_class: bool, in_function: bool) -> None:
        nonlocal in_main
        for child in node.children:
            t = child.type
            if t in GUARDS:
                cond = child.child_by_field_name("condition")
                guards.append("typing" if t == "if_statement" and cond is not None and b"TYPE_CHECKING" in cond.text
                              else f"{GUARDS[t]}@{lines.line(child.start_byte)}")
            if t in ("import_statement", "import_from_statement", "future_import_statement"):
                ex.imports.extend(replace(i, guard=guard()) for i in _imports(child, rel_path, lines))
            elif t in ("function_definition", "class_definition") and not in_function:
                define(child, scope, owner, in_class)
            elif t == "decorated_definition" and not in_function:
                definition = child.child_by_field_name("definition")
                fixture = any(d.type == "decorator" and b"fixture" in d.text for d in child.children)
                sym_id = define(definition, scope, owner, in_class, fixture) if definition is not None else None
                for deco in child.children:
                    if deco.type != "decorator":
                        continue
                    walk(deco, scope, owner, in_class, in_function)  # calls inside the decorator expression
                    if sym_id is None:
                        continue
                    expr = next((c for c in deco.children if c.is_named), None)
                    if expr is not None and expr.type == "call":
                        expr = expr.child_by_field_name("function")
                    name = _dotted(expr) if expr is not None else None
                    if name:
                        ex.refs.append(RawRef(sym_id, name, lines.line(deco.start_byte), "decorates"))
            elif t == "expression_statement" and not in_class and not in_function and child.named_children \
                    and child.named_children[0].type == "assignment":
                left = child.named_children[0].child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    sym = symbol(child.named_children[0], f"{scope}.{_text(left)}", "variable")
                    ex.symbols.append(sym)
                    ex.edges.append(Edge("CONTAINS", owner, sym.id))
                    declared = _type_name(child.named_children[0].child_by_field_name("type"))
                    value = ("type", declared) if declared else value_of(child.named_children[0].child_by_field_name("right"))
                    if value:
                        ex.returns[sym.id] = value
                walk(child, scope, owner, in_class, in_function)
            elif t == "call":
                fn = child.child_by_field_name("function")
                name = _dotted(fn)
                head, _, method = name.partition(".") if name else ("", "", "")
                args = child.child_by_field_name("arguments")
                via_super = fn is not None and fn.type == "attribute" and fn.child_by_field_name("object").type == "call" \
                    and _dotted(fn.child_by_field_name("object").child_by_field_name("function")) == "super"
                if name == "getattr" and args is not None and args.named_children and args.named_children[0].type == "identifier":
                    mod = _text(args.named_children[0])
                    if mod not in ("self", "cls") and not is_local(mod):
                        ex.refs.append(RawRef(owner, mod, lines.line(child.start_byte), "reads"))
                if head in ("self", "cls"):
                    # self.m() resolves to the enclosing class's own m; self.attr.m() needs the attribute's type, so it is dropped
                    if classes and method and "." not in method:
                        self_calls.append((owner, f"{classes[-1]}.{method}", lines.line(child.start_byte), guard()))
                    elif classes and method.count(".") == 1:   # self.attr.m(): an edge if the attribute's class is shown
                        attr, _, m = method.partition(".")
                        ex.refs.append(RawRef(owner, m, lines.line(child.start_byte), "calls", guard=guard(),
                                              receiver=("attr", (classes[-1], attr))))
                elif name and not is_local(name):
                    ex.refs.append(RawRef(owner, name, lines.line(child.start_byte), "calls", entry=in_main, guard=guard()))
                elif via_super and classes:
                    ex.refs.append(RawRef(owner, _text(fn.child_by_field_name("attribute")), lines.line(child.start_byte),
                                          "calls", guard=guard(), receiver=("super", classes[-1])))
                elif name and method and "." not in method and types and head in types[-1]:
                    ex.refs.append(RawRef(owner, method, lines.line(child.start_byte), "calls", guard=guard(),
                                          receiver=types[-1][head]))
                else:
                    walk(child, scope, owner, in_class, in_function)
                    continue
                if args is not None:
                    walk(args, scope, owner, in_class, in_function)
            elif t == "if_statement" and not in_class and not in_function and b"__main__" in child.child_by_field_name("condition").text:
                in_main = True
                walk(child, scope, owner, in_class, in_function)
                in_main = False
            elif t == "with_item":
                expr = child.named_children[0] if child.named_children else None
                bound = None
                if expr is not None and expr.type == "as_pattern":
                    target = next((c for c in expr.named_children if c.type == "as_pattern_target"), None)
                    if target is not None and target.named_children and target.named_children[0].type == "identifier":
                        bound = _text(target.named_children[0])
                    expr = expr.named_children[0]
                value = value_of(expr)
                if value:   # a with statement calls both, whatever it binds
                    for dunder in ("__enter__", "__exit__"):
                        ex.refs.append(RawRef(owner, dunder, lines.line(child.start_byte), "calls", guard=guard(), receiver=value))
                if bound and scopes:
                    scopes[-1].add(bound)
                    types[-1].pop(bound, None)
                    if value:
                        types[-1][bound] = ("enter", value)
                walk(child, scope, owner, in_class, in_function)
            elif t == "assignment" and in_class and not in_function:   # a class-body annotation or default: `engine: Engine`
                left = child.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    declared = _type_name(child.child_by_field_name("type"))
                    value = ("type", declared) if declared else value_of(child.child_by_field_name("right"))
                    if value:
                        ex.returns.setdefault(symbol_id(rel_path, f"{scope}.{_text(left)}"), value)
                walk(child, scope, owner, in_class, in_function)
            elif t in ("assignment", "for_statement", "for_in_clause") and scopes:
                left = child.child_by_field_name("left")
                if t == "assignment" and left is not None and left.type == "attribute" and classes:
                    head, _, attr = (_dotted(left) or "").partition(".")
                    declared = _type_name(child.child_by_field_name("type"))
                    value = ("type", declared) if declared else value_of(child.child_by_field_name("right"))
                    if head == "self" and attr and "." not in attr and value:   # self.attr = Engine()
                        ex.returns.setdefault(symbol_id(rel_path, f"{classes[-1]}.{attr}"), value)
                if left is not None and left.type == "identifier":
                    declared = _type_name(child.child_by_field_name("type")) if t == "assignment" else None
                    value = ("type", declared) if declared else value_of(child.child_by_field_name("right")) if t == "assignment" else None
                    scopes[-1].add(_text(left))
                    # ponytail: the last assignment wins, whatever branch it sits in; an unknown value forgets the type
                    if value:
                        types[-1][_text(left)] = value
                    else:
                        types[-1].pop(_text(left), None)
                walk(child, scope, owner, in_class, in_function)
            elif t in ("return_statement", "yield") and types and child.named_children:
                value = value_of(child.named_children[0])
                if value:
                    ex.returns.setdefault(owner, value)   # a declared return type, set first, is never overridden
                walk(child, scope, owner, in_class, in_function)
            elif t == "identifier":
                parent, name = child.parent, _text(child)
                assigned = parent is not None and parent.type in ("assignment", "augmented_assignment", "keyword_argument") \
                    and parent.child_by_field_name("left" if parent.type != "keyword_argument" else "name") == child
                if not assigned and not is_local(name):
                    ex.refs.append(RawRef(owner, name, lines.line(child.start_byte), "names"))
            elif t == "attribute":
                name = _dotted(child)
                if name and name.split(".", 1)[0] not in ("self", "cls") and not is_local(name):
                    ex.refs.append(RawRef(owner, name, lines.line(child.start_byte), "references"))
                elif not name:
                    walk(child, scope, owner, in_class, in_function)
            else:
                walk(child, scope, owner, in_class, in_function)
            if t in GUARDS:
                guards.pop()

    def guard() -> str:
        return guards[-1] if guards else ""

    walk(tree.root_node, module_name, rel_path, False, False)
    defined = {s.qualified for s in ex.symbols}
    for owner, target, line, g in self_calls:
        if target in defined:
            ex.edges.append(Edge("CALLS", owner, symbol_id(rel_path, target), line, guard=g))
        else:   # not this class's own: perhaps a base's, which only the scanner can see
            cls, _, method = target.rpartition(".")
            ex.refs.append(RawRef(owner, method, line, "calls", guard=g, receiver=("class", cls)))
    return ex
