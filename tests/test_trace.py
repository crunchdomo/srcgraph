from tests.test_cli import built, run  # noqa: F401  (fixture)


def test_trace_prints_nested_graph_calls_only(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "src.handlers.h.view(1)")
    assert code == 0, out
    lines = out.splitlines()
    assert lines[0] == "src.handlers.h.view(req=1)"
    assert lines[1] == "  src.contracts.c.call(req=1)"
    assert lines[2].startswith("    src.contracts.c.Contract.send(self=<")
    assert lines[3] == "      src.gateway.client.post()"
    assert lines[4] == "        = None"
    assert lines[-1] == "= None"
    assert "logging" not in out


def test_trace_marks_exception_and_exits_1(built, capsys):
    code, out, _ = run(capsys, "trace", "--db", built, "src.boom.boom(3)")
    assert code == 1
    assert "src.boom.boom(x=3)" in out and "  src.boom.inner(x=3)" in out
    assert "    ! ValueError: bad 3" in out
    assert out.rstrip().endswith("! ValueError: bad 3")
