from tests.test_cli import built, run  # noqa: F401  (fixture)


def test_trace_prints_nested_graph_calls_only(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "src.handlers.h.view(1)")
    assert code == 0, out
    lines = out.splitlines()
    assert lines[0] == "src.handlers.h.view(req=1)"
    assert lines[1] == "  src.contracts.c.call(req=1)"
    assert lines[2].startswith("    src.contracts.c.Contract.send(self=<")
    assert lines[3] == "      src.gateway.client.post()"
    assert lines[4].startswith("        = None  (")
    assert lines[-1].startswith("= None  (")
    assert "logging" not in out


def test_trace_marks_exception_and_exits_1(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "src.boom.boom(3)")
    assert code == 1
    assert "src.boom.boom(x=3)" in out and "  src.boom.inner(x=3)" in out
    assert "    ! ValueError: bad 3" in out
    assert out.rstrip().endswith("! ValueError: bad 3")


def test_trace_collapses_repeated_calls_and_times_them(built, capsys):
    import re
    code, out, _ = run(capsys, "trace", "--db", built, "src.boom.loop(5)")
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "src.boom.loop(n=5)"
    assert lines[1] == "  src.boom.inner_ok(i=0)"
    assert re.fullmatch(r"    = 0  \(\d+\.\d ms\)", lines[2])
    assert re.fullmatch(r"  × 5 calls  \(\d+\.\d ms\)", lines[3])
    assert lines[4] == "  src.boom.inner_ok(i=4)"
    assert re.fullmatch(r"    = 4  \(\d+\.\d ms\)", lines[5])
    assert re.fullmatch(r"  = None  \(\d+\.\d ms\)", lines[6])
    assert len(lines) == 8


def test_trace_shortens_values(built, capsys):
    _, out, _ = run(capsys, "trace", "--db", built, "src.boom.echo(list(range(100)))")
    assert out.splitlines()[0] == "src.boom.echo(x=list[100])"
    _, out, _ = run(capsys, "trace", "--db", built, "src.boom.echo('a' * 100)")
    assert out.splitlines()[0] == "src.boom.echo(x='" + "a" * 58 + "…)"
    _, out, _ = run(capsys, "trace", "--db", built, "src.boom.echo(src.boom.Grid())")
    assert out.splitlines()[0] == "src.boom.echo(x=Grid(3×4))"


def test_trace_runs_a_pytest_node_id_with_the_test_at_the_root(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "tests/uses_call.py::test_call")
    assert code == 0, out
    lines = out.splitlines()
    assert lines[0] == "tests.uses_call.test_call()"
    assert lines[1] == "  src.contracts.c.call(req=1)"
    assert lines[3] == "      src.gateway.client.post()"
    assert lines[-1].startswith("  = None  (")   # no root line: pytest owns the result


def test_trace_of_a_failing_test_shows_the_assertion_and_exits_1(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "tests/uses_call.py::test_fails")
    assert code == 1
    lines = out.splitlines()
    assert lines[0] == "tests.uses_call.test_fails()"
    assert lines[-1].startswith("  ! AssertionError")


def test_trace_saves_events_with_symbol_ids_next_to_the_db(built, capsys):
    import glob, json, os
    run(capsys, "trace", "--db", built, "src.handlers.h.view(1)")
    files = glob.glob(os.path.join(os.path.dirname(built), "traces", "*.json"))
    assert len(files) == 1
    saved = json.load(open(files[0]))
    assert saved["input"] == "src.handlers.h.view(1)"
    calls = [e for e in saved["events"] if e["ev"] == "call"]
    assert calls[0]["id"] == "src/handlers/h.py::src.handlers.h.view"
