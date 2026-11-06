from srcgraph.model import Unit, Symbol, Edge, Extraction, symbol_id, unresolved_id, SYMBOL_KINDS


def test_ids():
    assert symbol_id("src/a.py", "a.f") == "src/a.py::a.f"
    assert unresolved_id("os.path.join") == "unresolved::os.path.join"


def test_extraction_defaults_are_independent():
    assert Extraction().units == [] and Extraction().units is not Extraction().units


def test_kinds():
    assert "resource" in SYMBOL_KINDS and "unresolved" in SYMBOL_KINDS
