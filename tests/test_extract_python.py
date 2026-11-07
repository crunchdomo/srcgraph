from pathlib import Path
from srcgraph.extractors import EXTRACTORS
from srcgraph.extractors.python import extract


def ex(rel):
    return extract(rel, Path("tests/fixtures/pyrepo", rel).read_bytes(), rel[:-3].replace("/", ".").removesuffix(".__init__"))


def _src(src: str):
    return extract("m.py", src.encode(), "m")


def test_calls_through_parameters_and_locals_are_dropped():
    e = _src("def f(entry, *rest, **kw):\n    conn = make()\n    entry.get(1)\n    conn.run()\n    rest.pop()\n"
             "    for row in rows():\n        row.save()\n")
    names = {r.name for r in e.refs}
    assert names == {"make", "rows"}
    assert not [d for d in e.edges if d.label == "CALLS"]


def test_module_level_names_inside_a_function_still_reference():
    e = _src("import os\nCFG = {}\ndef f(x):\n    os.path.join(x)\n    CFG.get(x)\n")
    assert {r.name for r in e.refs} == {"os.path.join", "CFG.get"}


def test_self_and_cls_calls_resolve_to_the_enclosing_class_method():
    e = _src("class C:\n    def a(self):\n        self.b()\n        cls_free = 1\n"
             "    def b(self):\n        pass\n    @classmethod\n    def c(cls):\n        cls.b()\n")
    calls = {(d.src, d.dst, d.line) for d in e.edges if d.label == "CALLS"}
    assert ("m.py::m.C.a", "m.py::m.C.b", 3) in calls
    assert ("m.py::m.C.c", "m.py::m.C.b", 9) in calls
    assert not any(r.name.startswith(("self.", "cls.")) for r in e.refs)


def test_self_calls_to_missing_or_chained_methods_are_dropped():
    e = _src("class C:\n    def a(self):\n        self.missing()\n        self.engine.start()\n")
    assert not [d for d in e.edges if d.label == "CALLS"]
    assert not any(r.name.startswith("self.") for r in e.refs)


def test_registry():
    assert EXTRACTORS[".py"] is extract


def test_unit_and_symbols():
    e = ex("src/contracts/c.py")
    assert e.units[0].name == "src.contracts.c" and e.units[0].kind == "module"
    assert {(s.qualified, s.kind) for s in e.symbols} == {
        ("src.contracts.c.Contract", "class"), ("src.contracts.c.Contract.send", "method"),
        ("src.contracts.c.call", "function"), ("src.contracts.c.warm", "function")}
    send = next(s for s in e.symbols if s.name == "send")
    assert send.id == "src/contracts/c.py::src.contracts.c.Contract.send" and send.line == 4 and send.ast_kind == "function_definition"
    assert ("CONTAINS", "src/contracts/c.py::src.contracts.c.Contract", send.id) in {(d.label, d.src, d.dst) for d in e.edges}


def test_package_unit():
    assert ex("src/contracts/__init__.py").units[0].kind == "package"


def test_imports():
    e = ex("src/handlers/h.py")
    assert [(i.module, i.level, i.names, i.alias) for i in e.imports] == [
        ("logging", 0, (), None), ("src.contracts", 0, (("c", "c"),), None), ("src.db.q", 0, (("run", "db_run"),), None)]


def test_calls_including_module_scope():
    e = ex("src/handlers/h.py")
    assert {(r.src_id, r.name) for r in e.refs} == {
        ("src/handlers/h.py::src.handlers.h.view", "c.call"), ("src/handlers/h.py::src.handlers.h.view", "logging.info"),
        ("src/handlers/h.py::src.handlers.h.leak", "db_run"), ("src/handlers/h.py", "c.warm")}


def test_syntax_error_does_not_crash():
    e = ex("src/broken.py")
    assert e.units[0].path == "src/broken.py" and any(s.name == "ok" for s in e.symbols)


def test_multi_name_import():
    e = extract("m.py", b"import os, src.gateway.client as gc, sys\n", "m")
    assert [(i.module, i.alias) for i in e.imports] == [("os", None), ("src.gateway.client", "gc"), ("sys", None)]


def test_non_call_attribute_reference():
    e = extract("m.py", b"from src.gateway import client\nsession = client.session\n\nclass K:\n    def m(self):\n        return self.x.y\n", "m")
    assert [(r.src_id, r.name, r.relation) for r in e.refs] == [("m.py", "client.session", "references")]


def test_attribute_chains_do_not_corrupt_memory(tmp_path):
    """Regression: reading node.start_point once per reference (thousands of Point objects)
    corrupts the heap in py-tree-sitter 0.26 and crashes the interpreter at exit (SIGSEGV/SIGBUS).
    Must run in a subprocess because the crash is at teardown."""
    import os, subprocess, sys
    lines = ["import os", "class C:", "    def m(self):", "        pass", ""]
    for i in range(2000):
        lines += [f'v{i} = os.path.join("a", str({i})).strip().lower()',
                  f'os.environ.get("K{i}").x.y.z()', f"w{i} = sys.argv.count.__doc__"]
    src = tmp_path / "trigger.py"
    src.write_text("\n".join(lines) + "\n")
    # Keep only the refs: an Extraction kept alive until exit masks the crash.
    code = ("from srcgraph.extractors.python import extract\n"
            f"refs = extract('t.py', open({str(src)!r}, 'rb').read(), 't').refs\n"
            "assert any(r.name == 'os.environ.get' and r.relation == 'calls' for r in refs)\n"
            "assert any(r.name == 'sys.argv.count.__doc__' and r.relation == 'references' for r in refs)\n")
    proc = subprocess.run([sys.executable, "-c", code], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                          cwd=str(tmp_path), env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])})
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[-500:]}"


def test_syntax_tier_for_contracts():
    e = ex("src/contracts/c.py")
    kinds = {a.kind for a in e.ast}
    assert "module" in kinds and "class_definition" in kinds and "import_from_statement" in kinds
    cls = next(a for a in e.ast if a.kind == "class_definition")
    assert cls.norm == "ClassDef" and cls.text == "" and cls.line == 3
    attr = next(a for a in e.ast if a.kind == "attribute")
    assert attr.norm == "Attribute" and attr.text == "client.post"
    by_id = {a.id: a for a in e.ast}
    names = [by_id[d.dst] for d in e.ast_edges if d.label == "CHILD" and d.src == cls.id and by_id[d.dst].field == "name"]
    assert [n.text for n in names] == ["Contract"]
    nxt = [(by_id[d.src].kind, by_id[d.dst].kind) for d in e.ast_edges if d.label == "NEXT" and by_id[d.src].field == "" and by_id[d.src].line == 1]
    assert ("import_from_statement", "class_definition") in nxt


def test_derived_from_points_at_definition_not_decorator():
    e = extract("m.py", b"@dec\ndef f():\n    pass\n", "m")
    sym = next(s for s in e.symbols if s.name == "f")
    link = next(d for d in e.edges if d.label == "DERIVED_FROM" and d.src == sym.id)
    assert link.dst.endswith(":function_definition")
    assert any(d.label == "DERIVED_FROM" and d.src == "m.py" and d.dst.endswith(":module") for d in e.edges)


def test_norm_is_own_type_only():
    e = extract("m.py", b"def f(x, y=1):\n    pass\n", "m")
    by_kind = {}
    for a in e.ast:
        by_kind.setdefault(a.kind, []).append(a)
    assert all(a.norm == "Name" for a in by_kind["identifier"])
    assert by_kind["default_parameter"][0].norm == "Parameter"


def test_text_truncated_and_escaped():
    e = extract("m.py", b'x = "' + b"a" * 600 + b'"\ny = """l1\nl2"""\n', "m")
    strings = [a for a in e.ast if a.kind == "string"]
    assert len(strings[0].text) == 512 and strings[1].text == '"""l1\nl2"""'   # escaping happens in graph.py, not here


def test_inherits_and_decorates_refs():
    e = ex("src/views.py")
    rel = {(r.src_id.split("::")[1], r.name, r.relation) for r in e.refs if r.relation in ("inherits", "decorates")}
    assert ("src.views.View", "Base", "inherits") in rel and ("src.views.View", "Contract", "inherits") in rel
    assert ("src.views.View.get", "cache", "decorates") in rel          # method decorator, from the method
    assert ("src.views.handler", "route", "decorates") in rel           # call-form decorator uses the callee
    assert ("src.views.handler", "functools.wraps", "decorates") in rel
    assert ("src.views.Dyn", "make_base()", "inherits") in rel


def test_class_without_bases_has_no_inherits_edge():
    e = ex("src/views.py")
    assert not any(r.relation == "inherits" and r.src_id.endswith("src.views.Plain") for r in e.refs)


def test_decorator_expressions_still_produce_calls():
    e = extract("m.py", b'@app.route("/x", methods=helper())\ndef f():\n    pass\n', "m")
    calls = {(r.src_id, r.name) for r in e.refs if r.relation == "calls"}
    assert ("m.py", "app.route") in calls and ("m.py", "helper") in calls


def test_inherits_skips_keywords_comments_splats_and_unwraps_subscripts():
    src = b"import abc\nclass M(abc.ABC,  # a comment\n        metaclass=abc.ABCMeta, *extra, Generic[T]):\n    pass\n"
    e = extract("m.py", src, "m")
    assert sorted(r.name for r in e.refs if r.relation == "inherits") == ["Generic", "abc.ABC"]


def test_future_import_is_recorded():
    e = extract("m.py", b"from __future__ import annotations\nimport os\n", "m")
    assert [(i.module, i.names) for i in e.imports] == [("__future__", (("annotations", "annotations"),)), ("os", ())]


def _extract(src: str):
    from srcgraph.extractors.python import extract
    return extract("m.py", src.encode(), "m")


def test_module_doc_is_first_paragraph():
    ex = _extract('"""\nHelpers for sector analysis.\n\nLong detail that is\nnot the summary.\n"""\nX = 1\n')
    assert ex.units[0].doc == "Helpers for sector analysis."


def test_symbol_doc_for_function_class_and_none():
    ex = _extract(
        "class C:\n    'Single-quoted class doc.'\n    def m(self):\n        r\"\"\"\n        Raw and indented\n        over two lines.\n        \"\"\"\n"
        "def bare():\n    return 1\n")
    doc = {s.name: s.doc for s in ex.symbols}
    assert doc == {"C": "Single-quoted class doc.", "m": "Raw and indented over two lines.", "bare": ""}


def test_module_without_docstring_has_empty_doc():
    assert _extract("x = 1\n").units[0].doc == ""


def test_call_refs_carry_the_nearest_enclosing_guard():
    ex = _src("def f(x):\n    if x:\n        g()\n    for i in x:\n        h()\n    try:\n        m()\n    except E:\n        n()\n    while x:\n        w()\n    k()\n")
    guards = {r.name: r.guard for r in ex.refs if r.relation == "calls"}
    assert guards == {"g": "if@2", "h": "for@4", "m": "try@6", "n": "try@6", "w": "while@10", "k": ""}


def test_guard_does_not_leak_into_a_nested_definition():
    ex = _src("if cond:\n    def f():\n        g()\n    h()\n")
    guards = {r.name: r.guard for r in ex.refs if r.relation == "calls"}
    assert guards == {"g": "", "h": "if@1"}


def test_bare_names_used_as_values_or_annotations_are_name_refs():
    e = _src("def apply(fn, x):\n    return fn(x)\n\ndef g(y):\n    return y\n\napply(g, 1)\n"
             "def h(a: Thing, n=g) -> Thing:\n    pass\n\napply(fn=g, x=2)\ncount = 0\n")
    names = [(r.src_id, r.name) for r in e.refs if r.relation == "names"]
    assert names == [("m.py", "g"), ("m.py::m.h", "Thing"), ("m.py::m.h", "g"), ("m.py::m.h", "Thing"), ("m.py", "g")]
    # fn, x, y are parameters; count is being assigned; the keyword name fn is not a value
