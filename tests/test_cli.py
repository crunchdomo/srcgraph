import pytest
from srcgraph.cli import main


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_build_and_query(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir("tests/fixtures/pyrepo")
    db = str(tmp_path / "g.lbug")
    code, out, _ = run(capsys, "build", "--db", db)
    assert code == 0 and "syntax nodes" in out
    code, out, _ = run(capsys, "query", "--db", db, "MATCH (u:Unit {kind:'external'}) RETURN u.name AS n ORDER BY n")
    assert code == 0 and out.splitlines()[0] == "n" and "logging" in out


def test_query_without_build_exits_2(tmp_path, capsys):
    code, _, err = run(capsys, "query", "--db", str(tmp_path / "none.lbug"), "MATCH (n) RETURN n")
    assert code == 2 and "error:" in err


@pytest.fixture
def built(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir("tests/fixtures/pyrepo")
    db = str(tmp_path / "g.lbug")
    assert main(["build", "--db", db]) == 0
    capsys.readouterr()
    return db


def test_importers_lists_units_importing_a_module_or_its_submodules(built, capsys):
    code, out, _ = run(capsys, "importers", "--db", built, "logging")
    assert code == 0 and out.splitlines()[0] == "importer\tline\tmodule"
    assert any(l.startswith("src/handlers/h.py\t") and l.endswith("\tlogging") for l in out.splitlines())


def test_callers_lists_callers_with_lines(built, capsys):
    code, out, _ = run(capsys, "callers", "--db", built, "call")
    assert code == 0 and out.splitlines()[0] == "caller\tline\ttarget"
    assert any("src.handlers.h.view\t" in l and l.endswith("src.contracts.c.call") for l in out.splitlines())
    code, out, _ = run(capsys, "callers", "--db", built, "--format", "json", "src.contracts.c.call")
    assert code == 0 and '"caller"' in out


def test_preset_with_no_rows_exits_1(built, capsys):
    code, out, err = run(capsys, "importers", "--db", built, "nothing_imports_this")
    assert code == 1 and out == "" and "no importers" in err


def test_scope_shows_internal_outbound_and_inbound_wires(built, capsys):
    code, out, _ = run(capsys, "scope", "--db", built, "src/contracts/**")
    assert code == 0
    sec = {}
    for line in out.splitlines():
        if line.startswith("== "):
            cur = line.split()[1]; sec[cur] = []
        else:
            sec[cur].append(line)
    assert "src.contracts.c.call\tCALLS\tsrc.contracts.c.Contract\t8" in sec["internal"]
    assert "src.contracts.c.Contract.send\tCALLS\tsrc.gateway.client.post\t5" in sec["outbound"]
    assert "src/contracts/c.py\tIMPORTS\tsrc.gateway.client\t1" in sec["outbound"]
    assert "src.handlers.h.view\tCALLS\tsrc.contracts.c.call\t6" in sec["inbound"]
    assert "src/handlers/h.py\tIMPORTS\tsrc.contracts.c\t2" in sec["inbound"]


def test_scope_json_and_empty_glob(built, capsys):
    import json
    code, out, _ = run(capsys, "scope", "--db", built, "--format", "json", "src/contracts/**")
    assert code == 0 and set(json.loads(out)) == {"doc", "internal", "outbound", "inbound"}
    code, out, err = run(capsys, "scope", "--db", built, "nowhere/**")
    assert code == 1 and out == "" and "nothing matches" in err


def test_scope_leads_with_the_compartment_doc_line(tmp_path, monkeypatch, capsys):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "g.py").write_text('"""Gateway to the outside.\n\nDetail."""\n\ndef post():\n    pass\n')
    monkeypatch.chdir(tmp_path)
    db = str(tmp_path / "g.lbug")
    assert main(["build", "--db", db]) == 0
    capsys.readouterr()
    code, out, _ = run(capsys, "scope", "--db", db, "pkg/**")
    assert code == 0 and out.splitlines()[0] == "pkg/g.py\tGateway to the outside."
