import pytest
from srcgraph.graph import Graph
from srcgraph.model import Unit, Symbol, Edge, AstNode


def units():
    return [Unit("src/a.py", "src/a.py", "a", "python", "file"), Unit("src/b.py", "src/b.py", "b", "python", "file")]


def unit_rows():
    return [{**vars(u), "line": 1} for u in units()]


def test_load_and_query(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    s = Symbol("src/b.py::b.f", "f", "b.f", "function", "python", "src/b.py", 3, 5, "function_definition")
    g.load_nodes("Unit", unit_rows())
    g.load_nodes("Symbol", [vars(s)])
    g.load_edges([Edge("IMPORTS", "src/a.py", "src/b.py"), Edge("CALLS", "src/a.py", "src/b.py::b.f"),
                  Edge("CALLS", "src/a.py", "missing::x")])
    assert g.count("Unit") == 2 and g.count("Symbol") == 1
    rows = g.query("MATCH (a)-[:CALLS]->(b:Symbol) RETURN a.id AS src, b.id AS dst")
    assert rows == [{"src": "src/a.py", "dst": "src/b.py::b.f"}]
    assert g.query("MATCH (u:Unit) WHERE u.path =~ $re RETURN count(*) AS c", {"re": "src/a\\.py"}) == [{"c": 1}]
    g.close()


def test_reopen_is_fresh(tmp_path):
    p = str(tmp_path / "g.lbug")
    g = Graph(p); g.load_nodes("Unit", unit_rows()); g.close()
    g = Graph(p); assert g.count("Unit") == 0; g.close()


def test_edge_line_property(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    g.load_nodes("Unit", unit_rows())
    g.load_edges([Edge("IMPORTS", "src/a.py", "src/b.py", line=7)])
    assert g.query("MATCH ()-[r:IMPORTS]->() RETURN r.line AS l") == [{"l": 7}]
    g.close()


def test_ast_tier_and_derived_from(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    root = AstNode("src/a.py#0:20:module", "src/a.py", "module", "", "python", 1, 2, 0, 20, "", "")
    fn = AstNode("src/a.py#0:20:function_definition", "src/a.py", "function_definition", "FunctionDef", "python", 1, 2, 0, 20, "", "")
    name = AstNode("src/a.py#4:5:identifier", "src/a.py", "identifier", "Name", "python", 1, 1, 4, 5, "f", "name")
    s = Symbol("src/a.py::a.f", "f", "a.f", "function", "python", "src/a.py", 1, 2, "function_definition")
    g.load_nodes("Unit", unit_rows()); g.load_nodes("Symbol", [vars(s)]); g.load_nodes("Ast", [vars(a) for a in (root, fn, name)])
    g.load_edges([Edge("DERIVED_FROM", s.id, fn.id), Edge("DERIVED_FROM", "src/a.py", root.id),
                  Edge("CHILD", root.id, fn.id), Edge("CHILD", fn.id, name.id)],
                 props={("CHILD", fn.id, name.id): {"field": "name"}})
    assert g.count("Ast") == 3
    assert g.query("MATCH (:Symbol)-[:DERIVED_FROM]->(a:Ast) RETURN a.kind AS k") == [{"k": "function_definition"}]
    assert g.query("MATCH (:Ast {kind:'function_definition'})-[c:CHILD]->(n:Ast) RETURN c.field AS f, n.text AS t") == [{"f": "name", "t": "f"}]
    g.close()


def test_text_with_newlines_survives_copy(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    n = AstNode("x.py#0:9:string", "x.py", "string", "String", "python", 1, 2, 0, 9, "line1\nline2\r\n", "")
    g.load_nodes("Ast", [vars(n)])
    assert g.query("MATCH (a:Ast) RETURN a.text AS t") == [{"t": "line1\\nline2\\r\\n"}]
    g.close()


def test_repeated_load_appends(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    g.load_nodes("Unit", unit_rows())
    g.load_edges([Edge("IMPORTS", "src/a.py", "src/b.py")])
    g.load_edges([Edge("IMPORTS", "src/b.py", "src/a.py")])   # endpoints loaded in an earlier call
    assert g.count("Unit") == 2 and g.query("MATCH ()-[r:IMPORTS]->() RETURN count(*) AS c") == [{"c": 2}]
    g.close()


def test_empty_strings_load_as_empty_not_null(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    s = Symbol("unresolved::x", "x", "x", "unresolved", "python", "", 0, 0, "")
    g.load_nodes("Symbol", [vars(s)])
    assert g.query("MATCH (s:Symbol) WHERE s.path = '' RETURN count(*) AS c") == [{"c": 1}]
    g.close()


def test_extension_tables_and_generic_loads(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"),
              node_tables={"Decision": "id STRING, title STRING, PRIMARY KEY(id)"},
              rel_tables={"BINDS": ([("Decision", "Unit")], [])})
    g.load_nodes("Unit", unit_rows()[:1])
    g.load_nodes("Decision", [{"id": "ADR-1", "title": "t"}])
    g.load_edges([Edge("BINDS", "ADR-1", "src/a.py")])
    assert g.query("MATCH (d:Decision)-[:BINDS]->(u:Unit) RETURN d.id AS d, u.id AS u") == [{"d": "ADR-1", "u": "src/a.py"}]
    g.close()


def test_extension_cannot_shadow_own_table(tmp_path):
    with pytest.raises(ValueError, match="Unit"):
        Graph(str(tmp_path / "g.lbug"), node_tables={"Unit": "id STRING, PRIMARY KEY(id)"})


def test_load_code_sets_child_field(tmp_path, monkeypatch):
    from srcgraph.scan import scan
    monkeypatch.chdir("tests/fixtures/pyrepo")
    g = Graph(str(tmp_path / "g.lbug"))
    g.load_code(scan(["."]))
    assert g.count("Ast") > 100
    assert g.query("MATCH (:Ast {kind:'class_definition'})-[c:CHILD]->(n:Ast {kind:'identifier'}) WHERE c.field = 'name' RETURN n.text AS t ORDER BY t")[0]["t"] == "Base"
    g.close()


def test_open_then_load_with_extensions(tmp_path):
    p = str(tmp_path / "g.lbug")
    ext_nodes = {"Decision": "id STRING, title STRING, PRIMARY KEY(id)"}
    ext_rels = {"BINDS": ([("Decision", "Unit")], [])}
    g = Graph(p, node_tables=ext_nodes, rel_tables=ext_rels)
    g.load_nodes("Unit", unit_rows()); g.close()
    g = Graph.open(p, node_tables=ext_nodes, rel_tables=ext_rels)
    g.load_nodes("Decision", [{"id": "ADR-1", "title": "t"}])
    g.load_edges([Edge("BINDS", "ADR-1", "src/a.py"), Edge("IMPORTS", "src/a.py", "src/b.py")])
    assert g.query("MATCH (d:Decision)-[:BINDS]->(u:Unit) RETURN u.id AS u") == [{"u": "src/a.py"}]
    assert g.query("MATCH ()-[:IMPORTS]->() RETURN count(*) AS c") == [{"c": 1}]
    g.close()


def test_unknown_rel_property_is_a_clear_error(tmp_path):
    with pytest.raises(ValueError, match="weight"):
        Graph(str(tmp_path / "g.lbug"), rel_tables={"LIKES": ([("Unit", "Unit")], ["weight"])})


def test_doc_is_stored_on_unit_and_symbol(tmp_path):
    from srcgraph.model import Extraction, Unit, Symbol
    ex = Extraction(units=[Unit("m.py", "m.py", "m", "python", "module", doc="Module doc.")],
                    symbols=[Symbol("m.py::m.f", "f", "m.f", "function", "python", "m.py", 1, 2, "function_definition", doc="Func doc.")])
    g = Graph(str(tmp_path / "g.lbug")); g.load_code(ex)
    assert g.query("MATCH (u:Unit) RETURN u.doc AS d") == [{"d": "Module doc."}]
    assert g.query("MATCH (s:Symbol) RETURN s.doc AS d") == [{"d": "Func doc."}]
    g.close()


def test_test_and_entry_flags_are_stored(tmp_path):
    from srcgraph.model import Extraction, Unit, Symbol
    ex = Extraction(units=[Unit("tests/t.py", "tests/t.py", "tests.t", "python", "module", test=True)],
                    symbols=[Symbol("tests/t.py::tests.t.f", "f", "tests.t.f", "function", "python", "tests/t.py", 1, 2, "function_definition", entry=True)])
    g = Graph(str(tmp_path / "g.lbug")); g.load_code(ex)
    assert g.query("MATCH (u:Unit) RETURN u.test AS t") == [{"t": True}]
    assert g.query("MATCH (s:Symbol) RETURN s.entry AS e") == [{"e": True}]
    g.close()


def test_calls_edge_round_trips_its_guard(tmp_path):
    g = Graph(str(tmp_path / "g.lbug"))
    s = Symbol("src/b.py::b.f", "f", "b.f", "function", "python", "src/b.py", 3, 5, "function_definition")
    g.load_nodes("Unit", unit_rows())
    g.load_nodes("Symbol", [vars(s)])
    g.load_edges([Edge("CALLS", "src/a.py", "src/b.py::b.f", 7, guard="if@6")])
    assert g.query("MATCH ()-[r:CALLS]->() RETURN r.line AS line, r.guard AS guard") == [{"line": 7, "guard": "if@6"}]
    g.close()
