"""Python extractor: tree-sitter AST -> shared vocabulary."""
import posixpath
import re

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
    in_main = False                               # walking the body of a module-level `if __name__ == "__main__"`

    def is_local(name: str) -> bool:
        return bool(scopes) and name.partition(".")[0] in scopes[-1]

    def params(node: Node) -> set[str]:
        p = node.child_by_field_name("parameters")
        ids = [next((x for x in c.children if x.type == "identifier"), c) for c in (p.named_children if p is not None else [])]
        return {_text(i) for i in ids if i.type == "identifier"}

    def symbol(node: Node, qualified: str, kind: str, doc: str = "") -> Symbol:
        name = qualified.rsplit(".", 1)[-1]
        sym = Symbol(symbol_id(rel_path, qualified), name, qualified, kind, LANG, rel_path,
                     lines.line(node.start_byte), lines.line(max(node.end_byte - 1, node.start_byte)), node.type, doc)
        ex.edges.append(Edge("DERIVED_FROM", sym.id, ast_id(rel_path, node)))
        return sym

    GUARDS = {"if_statement": "if", "for_statement": "for", "while_statement": "while", "try_statement": "try"}
    guards: list[str] = []

    def define(node: Node, scope: str, owner: str, in_class: bool) -> str | None:
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
            guards.append("")
            walk(body, qualified, sym.id, is_class, not is_class)
            guards.pop()
            stack.pop()
        return sym.id

    def walk(node: Node, scope: str, owner: str, in_class: bool, in_function: bool) -> None:
        nonlocal in_main
        for child in node.children:
            t = child.type
            if t in GUARDS:
                guards.append(f"{GUARDS[t]}@{lines.line(child.start_byte)}")
            if t in ("import_statement", "import_from_statement", "future_import_statement"):
                ex.imports.extend(_imports(child, rel_path, lines))
            elif t in ("function_definition", "class_definition") and not in_function:
                define(child, scope, owner, in_class)
            elif t == "decorated_definition" and not in_function:
                definition = child.child_by_field_name("definition")
                sym_id = define(definition, scope, owner, in_class) if definition is not None else None
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
                walk(child, scope, owner, in_class, in_function)
            elif t == "call":
                name = _dotted(child.child_by_field_name("function"))
                head, _, method = name.partition(".") if name else ("", "", "")
                args = child.child_by_field_name("arguments")
                if head in ("self", "cls"):
                    # self.m() resolves to the enclosing class's own m; self.attr.m() needs the attribute's type, so it is dropped
                    if classes and method and "." not in method:
                        self_calls.append((owner, f"{classes[-1]}.{method}", lines.line(child.start_byte), guard()))
                elif name and not is_local(name):
                    ex.refs.append(RawRef(owner, name, lines.line(child.start_byte), "calls", entry=in_main, guard=guard()))
                else:
                    walk(child, scope, owner, in_class, in_function)
                    continue
                if args is not None:
                    walk(args, scope, owner, in_class, in_function)
            elif t == "if_statement" and not in_class and not in_function and b"__main__" in child.child_by_field_name("condition").text:
                in_main = True
                walk(child, scope, owner, in_class, in_function)
                in_main = False
            elif t in ("assignment", "for_statement", "for_in_clause") and scopes:
                left = child.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    scopes[-1].add(_text(left))
                walk(child, scope, owner, in_class, in_function)
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
    ex.edges.extend(Edge("CALLS", owner, symbol_id(rel_path, target), line, guard=g)
                    for owner, target, line, g in self_calls if target in defined)
    return ex
