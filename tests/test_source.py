from srcgraph.source import Source


def test_lines_missing_and_cache(tmp_path):
    (tmp_path / "a.py").write_text("one\ntwo\n")
    s = Source(str(tmp_path))
    assert s.lines("a.py") == ["one", "two"]
    assert s.lines("missing.py") is None
    (tmp_path / "a.py").unlink()
    assert s.lines("a.py") == ["one", "two"]   # cached


def test_lines_outside_root_is_none(tmp_path):
    (tmp_path / "root").mkdir()
    (tmp_path / "secret.txt").write_text("x\n")
    assert Source(str(tmp_path / "root")).lines("../secret.txt") is None
