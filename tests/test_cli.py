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
