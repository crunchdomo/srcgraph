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
    assert code == 0 and out.splitlines()[0] == "caller\tline\ttarget\tguard"
    assert any("src.handlers.h.view\t" in l and l.endswith("src.contracts.c.call\t") for l in out.splitlines())
    code, out, _ = run(capsys, "callers", "--db", built, "--format", "json", "src.contracts.c.call")
    assert code == 0 and '"caller"' in out


def test_preset_with_no_rows_exits_1(built, capsys):
    code, out, err = run(capsys, "importers", "--db", built, "nothing_imports_this")
    assert code == 1 and out == "" and "no importers" in err


def test_scope_shows_internal_outbound_and_inbound_wires(built, capsys):
    code, out, _ = run(capsys, "scope", "--db", built, "src/contracts/**")
    assert code == 0
    sec, cur = {}, None
    for line in out.splitlines():
        if line.startswith("== "):
            cur = line.split()[1]; sec[cur] = []
        elif cur:   # lines before the first section are the file header
            sec[cur].append(line)
    assert "src.contracts.c.call\tCALLS\tsrc.contracts.c.Contract\t8\t" in sec["internal"]
    assert "src.contracts.c.Contract.send\tCALLS\tsrc.gateway.client.post\t5\t" in sec["outbound"]
    assert "src/contracts/c.py\tIMPORTS\tsrc.gateway.client\t1\t" in sec["outbound"]
    assert "src.handlers.h.view\tCALLS\tsrc.contracts.c.call\t6\t" in sec["inbound"]
    assert "src/handlers/h.py\tIMPORTS\tsrc.contracts.c\t2\t" in sec["inbound"]


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


def test_dead_lists_unreached_symbols_only(built, capsys):
    code, out, _ = run(capsys, "dead", "--db", built)
    assert code == 0 and out.splitlines()[0] == "symbol\tpath\tline"
    names = {l.split("\t")[0] for l in out.splitlines()[1:]}
    assert "src.handlers.h.leak" in names
    assert "src.contracts.c.call" not in names   # called by view
    assert "src.views.Base" not in names   # inherited by View
    assert "src.views.Repr.__repr__" not in names   # dunders are called by the interpreter
    assert "tests.uses_call.test_call" not in names   # test code is reached by the runner
    assert "src.boom.Shape.area" not in names   # s.area() somewhere: a method with its name called on an instance
    assert "src.boom.Shape.never" in names
    assert "src.views.handler" not in names   # decorated: a framework registered it
    assert "src.views.Parser.handle_data" not in names   # overrides a method of an external base: the framework calls it
    assert "src.views.Parser.helper" not in names   # the graph cannot tell an override from a plain method there
    assert "src.boom.by_name" not in names   # passed to apply() as a value
    assert "src.views.Plain" not in names   # used in an annotation


def test_hubs_ranks_by_fan_in_plus_fan_out(built, capsys):
    code, out, _ = run(capsys, "hubs", "--db", built)
    assert code == 0 and out.splitlines()[0] == "node\tfan_in\tfan_out"
    totals = [int(a) + int(b) for _, a, b in (l.split("\t") for l in out.splitlines()[1:])]
    assert totals and totals == sorted(totals, reverse=True)
    assert any(l.startswith("src/gateway/client.py\t2\t") for l in out.splitlines())
    assert "src.handlers.h.view\t0\t1" in out.splitlines()   # logging.info is unresolved and not a wire
    assert "src.boom.echo\t1\t0" in out.splitlines()   # twice calls echo twice: one neighbour, not two call sites


def test_untested_without_test_units_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir("tests/fixtures/pyrepo")
    db = str(tmp_path / "g.lbug")
    assert main(["build", "--db", db, "--src", "src"]) == 0
    capsys.readouterr()
    code, out, err = run(capsys, "untested", "--db", db)
    assert code == 1 and out == "" and "no test units" in err


def test_untested_omits_symbols_reached_from_a_test_unit(built, capsys):
    code, out, _ = run(capsys, "untested", "--db", built)
    assert code == 0 and out.splitlines()[0] == "symbol\tpath\tline"
    names = {l.split("\t")[0] for l in out.splitlines()[1:]}
    assert "src.handlers.h.leak" in names
    assert "src.contracts.c.call" not in names   # tests/uses_call.py calls it
    assert not any(n.startswith("tests.") for n in names)   # test code is not itself untested
    assert "src.contracts.c.Contract.send" in names   # reaching the class does not reach its methods


def test_scope_lists_every_file_in_the_compartment_documented_or_not(built, capsys):
    code, out, _ = run(capsys, "scope", "--db", built, "src/contracts/**")
    lines = out.splitlines()
    head = lines[:next(i for i, l in enumerate(lines) if l.startswith("== "))]
    assert head == ["src/contracts/__init__.py\t", "src/contracts/c.py\t"]   # no docstrings, still listed


def test_scope_no_tests_hides_wires_touching_test_units(built, capsys):
    _, out, _ = run(capsys, "scope", "--db", built, "src/contracts/**")
    assert any(l.startswith("tests/uses_call.py\tIMPORTS") for l in out.splitlines())
    code, out, _ = run(capsys, "scope", "--db", built, "--no-tests", "src/contracts/**")
    assert code == 0
    assert not any(l.startswith(("tests/", "tests.")) for l in out.splitlines())
    assert "src.handlers.h.view\tCALLS\tsrc.contracts.c.call\t6\t" in out


def test_callers_and_scope_show_the_guard_column(built, capsys):
    code, out, _ = run(capsys, "callers", "--db", built, "inner_ok")
    assert code == 0 and out.splitlines()[0] == "caller\tline\ttarget\tguard"
    assert "src.boom.loop\t12\tsrc.boom.inner_ok\tfor@11" in out.splitlines()
    code, out, _ = run(capsys, "scope", "--db", built, "src/boom.py")
    assert code == 0 and "== internal" in out
    assert "src.boom.loop\tCALLS\tsrc.boom.inner_ok\t12\tfor@11" in out.splitlines()
    assert "src.boom.boom\tCALLS\tsrc.boom.inner\t2\t" in out.splitlines()


def test_empty_callers_says_how_much_the_graph_sees(built, capsys):
    code, out, err = run(capsys, "callers", "--db", built, "nothing_calls_this")
    assert code == 1 and out == "" and "no callers of nothing_calls_this" in err
    assert "calls resolved" in err and "self" in err   # the error bar and the blind spot, only when the answer is empty
    with pytest.raises(SystemExit):   # the standalone table is gone
        main(["visibility", "--db", built])
DOMAINS = ["handlers=src/handlers/**", "contracts=src/contracts/**", "db=src/db/**", "gateway=src/gateway/**"]


def test_domains_rolls_wires_up_to_named_groups_and_lists_the_crossings(built, capsys):
    code, out, _ = run(capsys, "domains", "--db", built, *DOMAINS)
    assert code == 0
    matrix, crossings = out.split("== crossings")
    assert matrix.splitlines()[0] == "from\tto\tedges"
    assert any(line.startswith("handlers\tcontracts\t") for line in matrix.splitlines())
    assert "handlers\tcontracts\tsrc.handlers.h.view\tCALLS\tsrc.contracts.c.call\t6" in crossings
    assert "contracts\tgateway\tsrc.contracts.c.Contract.send\tCALLS\tsrc.gateway.client.post\t5" in crossings
    assert "contracts\tcontracts" not in out   # internal wires are not crossings


def test_domains_json_bad_spec_and_no_match(built, capsys):
    import json
    code, out, _ = run(capsys, "domains", "--db", built, "--format", "json", *DOMAINS)
    assert code == 0 and set(json.loads(out)) == {"matrix", "crossings"}
    code, _, err = run(capsys, "domains", "--db", built, "nothing/**")
    assert code == 2 and "name=glob" in err
    code, out, err = run(capsys, "domains", "--db", built, "x=nowhere/**")
    assert code == 1 and out == "" and "no wires cross" in err


def test_dead_spares_public_functions_of_a_module_read_through_getattr(built, capsys):
    _, out, _ = run(capsys, "dead", "--db", built)
    names = {line.split("\t")[0] for line in out.splitlines()[1:]}
    assert "src.contracts.c.plug" not in names and "src.contracts.c._hidden" in names


def test_callers_matches_a_dotted_suffix_of_the_qualified_name(built, capsys):
    _, full, _ = run(capsys, "callers", "--db", built, "src.contracts.c.call")
    code, out, _ = run(capsys, "callers", "--db", built, "c.call")
    assert code == 0 and out == full and "src.handlers.h.view\t" in out
