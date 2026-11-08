import os
import pytest
import srcgraph

ROOT = os.path.abspath("tests/fixtures/pyrepo")


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("api") / "g.lbug")
    with srcgraph.load(ROOT, db=db) as g:   # loaded from the srcgraph checkout, not from inside the fixture
        yield g


def test_load_builds_relative_to_the_root_not_the_cwd(repo):
    assert {r["path"] for r in repo.dead()} >= {"src/handlers/h.py"}
    assert any(r["symbol"] == "src.handlers.h.leak" for r in repo.dead())


def test_presets_scope_and_query_return_the_cli_rows(repo):
    assert repo.callers("call")[0]["caller"] == "src.handlers.h.view"
    assert repo.importers("logging")[0]["importer"] == "src/handlers/h.py"
    assert repo.hubs()[0].keys() == {"node", "fan_in", "fan_out"}
    assert "src.contracts.c.call" not in {r["symbol"] for r in repo.untested()}
    sections = repo.scope("src/contracts/**")
    assert set(sections) == {"doc", "internal", "outbound", "inbound"} and sections["inbound"]
    assert repo.query("MATCH (u:Unit {test: true}) RETURN u.path AS p") == [{"p": "tests/uses_call.py"}]


def test_trace_runs_in_the_root(repo):
    events = repo.trace("src.handlers.h.view(1)")
    assert [e["sym"] for e in events if e["ev"] == "call"][:2] == ["src.handlers.h.view", "src.contracts.c.call"]


def test_load_reuses_an_existing_db_unless_rebuild(tmp_path, monkeypatch):
    import srcgraph.api as api
    db = str(tmp_path / "g.lbug")
    srcgraph.load(ROOT, db=db).close()
    monkeypatch.setattr(api, "build", lambda *a: pytest.fail("rebuilt although the db exists"))
    srcgraph.load(ROOT, db=db).close()
    monkeypatch.setattr(api, "build", lambda *a: (_ for _ in ()).throw(RuntimeError("rebuild requested")))
    with pytest.raises(RuntimeError, match="rebuild requested"):
        srcgraph.load(ROOT, db=db, rebuild=True)


def test_load_exposes_scan_warnings(tmp_path, capsys):
    with srcgraph.load(str(tmp_path), db=str(tmp_path / "g.lbug")) as g:
        assert any("no source files" in w for w in g.warnings)
    assert "warning: no source files" in capsys.readouterr().err
def test_domains_returns_matrix_and_crossings(repo):
    d = repo.domains({"handlers": "src/handlers/**", "contracts": "src/contracts/**", "gateway": "src/gateway/**"})
    edges = {(r["from"], r["to"]): r["edges"] for r in d["matrix"]}
    assert edges[("handlers", "contracts")] >= 2 and ("contracts", "handlers") not in edges
    assert {r["from"] for r in d["crossings"]} <= {"handlers", "contracts", "gateway"}
