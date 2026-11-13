"""Lua extractor: a file is a module, its functions are symbols, `require` is an import.

A module's export is the table its last statement returns (`local M = {} ... return M`), so `function M.send` in
app/net.lua is `app.net.send`, the name another file reaches it by. Functions on any other table keep the table's
name (`app.framing.Codec.encode`). Lua has no types, so a call is kept only when its head is something the file
can name: one of its own functions or tables, a `require`d module, or a global. A call on a local value is dropped."""
import tree_sitter_lua
from tree_sitter import Language, Node, Parser

from srcgraph.extractors.lines import LineIndex
from srcgraph.extractors.syntax import ast_id, emit_syntax, text as _text
from srcgraph.model import Edge, Extraction, RawImport, RawRef, Symbol, Unit, is_test_path, symbol_id

LANG = "lua"
_PARSER = Parser(Language(tree_sitter_lua.language()))
LUA_NORM = {
    "function_declaration": "FunctionDef", "function_definition": "Lambda", "function_call": "Call",
    "assignment_statement": "Assign", "dot_index_expression": "Attribute", "method_index_expression": "Attribute",
    "identifier": "Name", "string": "String", "return_statement": "Return", "if_statement": "If",
    "for_statement": "Loop", "while_statement": "Loop", "repeat_statement": "Loop", "block": "Block",
    "comment": "Comment", "number": "Literal", "true": "Literal", "false": "Literal", "nil": "Literal",
}
# Lua 5.1 / LuaJIT globals: functions and the standard library tables (string.format is a builtin by its head).
BUILTINS = frozenset((
    "assert collectgarbage dofile error getfenv getmetatable ipairs load loadfile loadstring module next pairs pcall "
    "print rawequal rawget rawlen rawset require select setfenv setmetatable tonumber tostring type unpack xpcall "
    "string table math os io coroutine debug package utf8 bit bit32 jit").split())
_GUARDS = {"if_statement": "if", "for_statement": "for", "while_statement": "while", "repeat_statement": "while"}
_INDEX = ("dot_index_expression", "method_index_expression")


def _dotted(node: Node | None) -> str | None:
    """`a.b.c` or `a.b:c` as a dotted name; None for anything that is not a chain of plain names."""
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node)
    if node.type in _INDEX:
        head = _dotted(node.child_by_field_name("table"))
        last = node.child_by_field_name("field") or node.child_by_field_name("method")
        return f"{head}.{_text(last)}" if head and last is not None else None
    return None


def _assignments(stmt: Node) -> list[tuple[Node, Node | None]]:
    """(target, value) pairs of `a, b = x, y`, with or without `local`; the value is None for a bare `local a`."""
    if stmt.type == "variable_declaration":
        stmt = next((c for c in stmt.named_children if c.type in ("assignment_statement", "variable_list")), stmt)
    if stmt.type == "variable_list":
        return [(t, None) for t in stmt.named_children]
    if stmt.type != "assignment_statement":
        return []
    targets = next((c for c in stmt.named_children if c.type == "variable_list"), None)
    values = next((c for c in stmt.named_children if c.type == "expression_list"), None)
    ts = targets.named_children if targets is not None else []
    vs = values.named_children if values is not None else []
    return [(t, vs[i] if i < len(vs) else None) for i, t in enumerate(ts)]


def _require(value: Node | None) -> tuple[str, str | None] | None:
    """(module, field) of `require('a/b')`, `require('a/b').field` or `pcall(require, 'a/b')`; the module in dotted form."""
    if value is None:
        return None
    if value.type == "dot_index_expression":
        inner = _require(value.child_by_field_name("table"))
        field = value.child_by_field_name("field")
        return (inner[0], _text(field)) if inner and inner[1] is None and field is not None else None
    if value.type != "function_call":
        return None
    args = value.child_by_field_name("arguments")
    given = list(args.named_children) if args is not None else []
    callee = _dotted(value.child_by_field_name("name"))
    if callee == "pcall" and given and _dotted(given[0]) == "require":
        given = given[1:]
    elif callee != "require":
        return None
    arg = given[0] if len(given) == 1 and given[0].type == "string" else None
    content = next((c for c in arg.named_children if c.type == "string_content"), None) if arg is not None else None
    return (_text(content).strip("/").replace("/", "."), None) if content is not None else None


def _comment_block(first: Node, lines: LineIndex) -> tuple[list[str], Node]:
    """The text of the comments running line after line from `first`, and the last of them."""
    out, node = [], first
    while True:
        body = _text(node).lstrip("-").strip()
        out.append(body.removeprefix("[[").removesuffix("]]").strip())
        nxt = node.next_named_sibling
        if nxt is None or nxt.type != "comment" or lines.line(nxt.start_byte) != lines.line(node.end_byte - 1) + 1:
            return out, node
        node = nxt


def _first_paragraph(text: list[str]) -> str:
    para = []
    for line in text:
        if not line and para:
            break
        para.append(line)
    return " ".join(" ".join(para).split())


def _doc_above(stmt: Node, lines: LineIndex) -> str:
    """First paragraph of the comment block that ends on the line above stmt; "" if there is none."""
    first = stmt.prev_named_sibling
    if first is None or first.type != "comment" or lines.line(first.end_byte - 1) != lines.line(stmt.start_byte) - 1:
        return ""
    while (prev := first.prev_named_sibling) is not None and prev.type == "comment" \
            and lines.line(prev.end_byte - 1) == lines.line(first.start_byte) - 1:
        first = prev
    return _first_paragraph(_comment_block(first, lines)[0])


def extract(rel_path: str, source: bytes, module_name: str) -> Extraction:
    ex = Extraction()
    tree = _PARSER.parse(source)
    root = tree.root_node
    lines = LineIndex(source)
    ex.ast, ex.ast_edges = emit_syntax(root, LUA_NORM, rel_path, LANG, lines)
    ex.edges.append(Edge("DERIVED_FROM", rel_path, ast_id(rel_path, root)))

    last = root.named_children[-1] if root.named_children else None
    returned = last.named_children[0].named_children if last is not None and last.type == "return_statement" and last.named_children else []
    export = _text(returned[0]) if len(returned) == 1 and returned[0].type == "identifier" else None

    def short(dotted: str) -> str:
        """The name inside this module: the export table's own name is dropped."""
        head, dot, rest = dotted.partition(".")
        return rest if dot and head == export else dotted

    # what the top-level statements define: (statement, the node that is the function or the other name's value,
    # name inside the module, kind, the function it is another name for, the table it sits on)
    defs: list[tuple[Node, Node, str, str, str | None, str | None]] = []
    for stmt in root.named_children:
        if stmt.type == "function_declaration":
            name_node = stmt.child_by_field_name("name")
            name = _dotted(name_node)
            if name:
                kind = "method" if name_node.type == "method_index_expression" else "function"
                defs.append((stmt, stmt, short(name), kind, None, name.rpartition(".")[0] or None))
        for target, value in _assignments(stmt):
            name = _dotted(target)
            if name and value is not None and value.type == "function_definition":
                defs.append((stmt, value, short(name), "function", None, name.rpartition(".")[0] or None))
            elif name and "." in name and _dotted(value):
                defs.append((stmt, value, short(name), "function", short(_dotted(value)), None))
    functions = {name for _, _, name, _, alias, _ in defs if alias is None}
    tables = {name.rpartition(".")[0] for name in functions if "." in name}
    # functions a bare name reaches: `local function f`, not `function M.f`, which only `M.f` names
    bare = {name for _, _, name, _, alias, table in defs if alias is None and table is None}
    # and the ones `M.f` reaches: defined on the export, or put there by `M.f = f`
    exported = {name for _, _, name, _, alias, table in defs if "." not in name and (table == export if alias is None else alias in functions)}

    first = root.named_children[0] if root.named_children else None
    unit_doc = ""
    if first is not None and first.type == "comment":
        text, end = _comment_block(first, lines)
        after = end.next_named_sibling
        owned = after is not None and any(s == after for s, *_ in defs) \
            and lines.line(after.start_byte) == lines.line(end.end_byte - 1) + 1
        unit_doc = "" if owned else _first_paragraph(text)   # a block sitting on a function is that function's doc
    ex.units.append(Unit(rel_path, rel_path, module_name, LANG, "module", doc=unit_doc, test=is_test_path(rel_path)))

    # names bound by require anywhere in the file
    # ponytail: file-wide, so a `local net = require(...)` inside one function names the module in every function
    bound: dict[int, tuple[str, str | None, str | None]] = {}   # the require's start byte -> (module, field, local name)
    aliases: set[str] = set()

    def find_requires(node: Node) -> None:
        pairs = _assignments(node)
        for i, (target, value) in enumerate(pairs):
            req = _require(value)
            if req and _dotted(value.child_by_field_name("name")) == "pcall":   # local ok, m = pcall(require, 'm')
                target = pairs[i + 1][0] if i + 1 < len(pairs) else None
            if req and target is not None and target.type == "identifier":
                call = value if value.type == "function_call" else value.child_by_field_name("table")
                bound[call.start_byte] = (*req, _text(target))
                aliases.add(_text(target))
        for c in node.named_children:
            find_requires(c)

    find_requires(root)

    file_locals: set[str] = set()
    scopes: list[set[str]] = []    # parameter and local names of the functions being walked, innermost last
    guards: list[str] = []

    def guard() -> str:
        return guards[-1] if guards else ""

    def declare(name: str) -> None:
        if name not in aliases:
            (scopes[-1] if scopes else file_locals).add(name)

    def reach(name: str, self_table: str | None) -> str | None:
        """The name a use of `name` is recorded under, or None when it is a local value or nothing this file can name."""
        head, dot, rest = name.partition(".")
        if head == "self":
            if not self_table:
                return None
            name = f"{self_table}.{rest}" if dot else self_table
            head = name.partition(".")[0]
        elif any(head in s for s in scopes):
            return None
        if head in aliases:
            return name
        if "." not in name and name in functions and name not in bare:
            return None   # `local state` beside `function M.state`: the bare name is the value, not the function
        if head == export and "." not in short(name) and short(name) in functions and short(name) not in exported:
            return None   # `M.gps` beside `local function gps`: a field of the export, not the local function
        if short(name) in functions:
            return short(name)
        if head == export or head in tables or head in functions or head in file_locals:
            return None
        return name   # a global

    def inline(node: Node | None, owner: str, relation: str) -> bool:
        """`require('m').f` used on the spot: an import under a name of its own, and a use of m.f."""
        req = _require(node) if node is not None and node.type == "dot_index_expression" else None
        if not req or req[1] is None or node.child_by_field_name("table").start_byte in bound:
            return False
        alias, line = "@" + req[0].replace(".", "/"), lines.line(node.start_byte)   # no dot: the scanner splits a name on its first
        ex.imports.append(RawImport(rel_path, req[0], 0, line, alias=alias))
        ex.refs.append(RawRef(owner, f"{alias}.{req[1]}", line, relation, guard=guard()))
        return True

    def params(fn: Node) -> set[str]:
        p = fn.child_by_field_name("parameters")
        return {_text(c) for c in (p.named_children if p is not None else []) if c.type == "identifier"}

    def body_of(fn: Node, owner: str, self_table: str | None) -> None:
        body = fn.child_by_field_name("body")
        if body is not None:
            scopes.append(params(fn))
            walk(body, owner, self_table)
            scopes.pop()

    def walk(node: Node, owner: str, self_table: str | None) -> None:
        for child in node.named_children:
            visit(child, owner, self_table)

    def visit(child: Node, owner: str, self_table: str | None) -> None:
        t = child.type
        if t in ("comment", "string", "parameters", "goto_statement", "label_statement"):
            return
        if t in _GUARDS:
            guards.append(f"{_GUARDS[t]}@{lines.line(child.start_byte)}")
        if t == "function_declaration":   # a nested one: top-level definitions are walked from their symbols
            name = child.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                declare(_text(name))
            body_of(child, owner, self_table)
        elif t == "function_definition":
            body_of(child, owner, self_table)
        elif t in ("variable_declaration", "assignment_statement"):
            pairs = _assignments(child)
            for target, value in pairs:
                if _dotted(target) is None:
                    visit(target, owner, self_table)
                if value is not None:
                    visit(value, owner, self_table)
            if t == "variable_declaration":
                for target, _ in pairs:
                    if target.type == "identifier":
                        declare(_text(target))
        elif t == "for_statement":
            clause = child.child_by_field_name("clause")
            names = [clause.child_by_field_name("name")] if clause is not None and clause.type == "for_numeric_clause" \
                else next((c.named_children for c in (clause.named_children if clause is not None else []) if c.type == "variable_list"), [])
            for n in names:
                if n is not None:
                    declare(_text(n))
            for c in (clause.named_children if clause is not None else []):
                if c.type != "variable_list" and c not in names:
                    visit(c, owner, self_table)
            body = child.child_by_field_name("body")
            if body is not None:
                walk(body, owner, self_table)
        elif t == "function_call":
            if child.start_byte in bound or _require(child):
                module, field, local = bound.get(child.start_byte) or (*_require(child), None)
                line = lines.line(child.start_byte)
                ex.imports.append(RawImport(rel_path, module, 0, line, names=((field, local),)) if field and local
                                  else RawImport(rel_path, module, 0, line, alias=local))
            else:
                callee = child.child_by_field_name("name")
                name = _dotted(callee)
                target = reach(name, self_table) if name else None
                if target:
                    ex.refs.append(RawRef(owner, target, lines.line(child.start_byte), "calls", guard=guard()))
                elif name is None and callee is not None and not inline(callee, owner, "calls"):
                    visit(callee, owner, self_table)
                args = child.child_by_field_name("arguments")
                if args is not None:
                    walk(args, owner, self_table)
        elif t == "field":   # { key = value }: the key is a name in the table, not a use
            key, value = child.child_by_field_name("name"), child.child_by_field_name("value")
            if key is not None and key.type != "identifier":
                visit(key, owner, self_table)
            if value is not None:
                visit(value, owner, self_table)
        elif t == "identifier" or t in _INDEX:
            name = _dotted(child)
            if name is None:   # a[i].b: only the table is an expression, the field is a key
                if not inline(child, owner, "names"):
                    visit(child.child_by_field_name("table"), owner, self_table)
            else:
                target = reach(name, self_table)
                if target and (target in functions or target.partition(".")[0] in aliases):
                    ex.refs.append(RawRef(owner, target, lines.line(child.start_byte), "names", guard=guard()))
        else:
            walk(child, owner, self_table)
        if t in _GUARDS:
            guards.pop()

    owners: dict[int, tuple[str, str | None, bool]] = {}   # a def's node -> (symbol or unit, self's table, walk its value)
    by_id: dict[str, Symbol] = {}
    for stmt, node, name, kind, alias, table in defs:
        owners[node.start_byte] = (rel_path, table, alias not in functions)
        if alias is not None and (alias not in functions or alias == name or name in functions):
            continue   # `M.send = send` is the same function under its export name, not a second one
        qualified = f"{module_name}.{name}"
        sym = Symbol(symbol_id(rel_path, qualified), name.rsplit(".", 1)[-1], qualified, kind, LANG, rel_path,
                     lines.line(stmt.start_byte), lines.line(max(stmt.end_byte - 1, stmt.start_byte)), stmt.type,
                     _doc_above(stmt, lines))
        if sym.id not in by_id:   # ponytail: a second definition of a name is folded into the first
            by_id[sym.id] = sym
            ex.symbols.append(sym)
            ex.edges.append(Edge("CONTAINS", rel_path, sym.id))
            ex.edges.append(Edge("DERIVED_FROM", sym.id, ast_id(rel_path, stmt)))
        owners[node.start_byte] = (sym.id, table, False)
        if alias is not None:   # another name for a function reaches that function
            ex.refs.append(RawRef(sym.id, alias, lines.line(stmt.start_byte), "calls"))

    for stmt in root.named_children:
        if stmt.type == "function_declaration" and stmt.start_byte in owners:
            owner, table, _ = owners[stmt.start_byte]
            body_of(stmt, owner, table)
            continue
        pairs = _assignments(stmt)
        if not any(value is not None and value.start_byte in owners for _, value in pairs):
            visit(stmt, rel_path, None)
            continue
        for target, value in pairs:
            if value is None:
                continue
            if value.start_byte not in owners:
                visit(value, rel_path, None)
                continue
            owner, table, walk_value = owners[value.start_byte]
            if value.type == "function_definition":
                body_of(value, owner, table)
            elif walk_value:   # `M.fmt = string.format`, `M.pack = framing.pack`: a use of something outside the file
                visit(value, rel_path, None)
        if stmt.type == "variable_declaration":
            for target, _ in pairs:
                if target.type == "identifier" and _text(target) not in functions:
                    declare(_text(target))
    return ex
