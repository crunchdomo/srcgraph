import json
import os
import pytest
from srcgraph.cli import main
from srcgraph import describe


@pytest.fixture
def built(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir("tests/fixtures/pyrepo")
    db = str(tmp_path / "g.lbug")
    assert main(["build", "--db", db]) == 0
    capsys.readouterr()
    return db


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_find_matches_names_and_doc_lines_case_insensitively(built, capsys):
    code, out, _ = run(capsys, "find", "--db", built, "LEAK")
    assert code == 0 and out.splitlines()[0] == "node\tpath\tline\tabout"
    assert any(l.startswith("src.handlers.h.leak\t") for l in out.splitlines())
    code, _, err = run(capsys, "find", "--db", built, "zzz-nothing")
    assert code == 1 and "nothing mentions zzz-nothing" in err


def test_describe_fills_summaries_caches_by_hash_and_survives_a_rebuild(built, capsys):
    from srcgraph.graph import Graph
    from srcgraph.source import Source
    calls = []

    def fake(kind, text):
        calls.append(kind)
        return f"a {kind} that does things"
    cache = describe.cache_path(built)
    with Graph.open(built) as g:
        assert describe.run(g, Source("."), cache, summarise=fake, out=None) == 0
        rows = g.query("MATCH (s:Symbol {qualified: 'src.handlers.h.leak'}) RETURN s.summary AS s, s.doc AS d")
    assert rows == [{"s": "a function that does things", "d": ""}] and "module" in calls and len(calls) > 1
    assert os.path.exists(cache) and len(json.load(open(cache))) == len(calls)
    # find sees the summary
    code, out, _ = run(capsys, "find", "--db", built, "does things")
    assert code == 0 and "src.handlers.h.leak" in out
    # second run: everything hashes the same, nothing is sent
    calls.clear()
    with Graph.open(built) as g:
        describe.run(g, Source("."), cache, summarise=fake, out=None)
    assert calls == []
    # a rebuild loads the cached summaries without the model
    assert main(["build", "--db", built]) == 0
    with Graph.open(built) as g:
        assert g.query("MATCH (s:Symbol {qualified: 'src.handlers.h.leak'}) RETURN s.summary AS s")[0]["s"] == "a function that does things"
    code, out, _ = run(capsys, "scope", "--db", built, "src/handlers/**")
    assert code == 0 and "does things" in out


def test_describe_without_the_sdk_says_how_to_install_it(built, monkeypatch, capsys):
    import builtins
    real = builtins.__import__

    def no_anthropic(name, *a, **k):
        if name == "anthropic":
            raise ImportError
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_anthropic)
    with pytest.raises(SystemExit, match="srcgraph\\[describe\\]"):
        main(["describe", "--db", built])
