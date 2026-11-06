import pytest
from srcgraph.scan import scan, module_name


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.chdir("tests/fixtures/pyrepo")
    return scan(["."])


def test_module_name():
    assert module_name(".", "src/a/b.py") == "src.a.b" and module_name(".", "src/a/__init__.py") == "src.a"


def test_imports_resolved_and_external(repo):
    e = {(d.label, d.src, d.dst) for d in repo.edges}
    assert ("IMPORTS", "src/handlers/h.py", "src/contracts/c.py") in e
    assert ("IMPORTS", "src/handlers/h.py", "src/db/q.py") in e
    assert ("IMPORTS", "src/handlers/h.py", "logging") in e
    ext = next(u for u in repo.units if u.id == "logging")
    assert ext.kind == "external" and ext.name == "logging"
    assert ("CONTAINS", "src/contracts/__init__.py", "src/contracts/c.py") in e


def test_calls_resolved(repo):
    e = {(d.label, d.src, d.dst) for d in repo.edges}
    assert ("CALLS", "src/handlers/h.py::src.handlers.h.view", "src/contracts/c.py::src.contracts.c.call") in e
    assert ("CALLS", "src/handlers/h.py::src.handlers.h.leak", "src/db/q.py::src.db.q.run") in e
    assert ("CALLS", "src/handlers/h.py", "src/contracts/c.py::src.contracts.c.warm") in e
    assert ("CALLS", "src/handlers/h.py::src.handlers.h.view", "unresolved::logging.info") in e
    assert any(s.id == "unresolved::logging.info" and s.kind == "unresolved" for s in repo.symbols)


def test_ambiguous_roots_warn(tmp_path, monkeypatch):
    for r in ("r1", "r2"):
        (tmp_path / r / "pkg").mkdir(parents=True)
        (tmp_path / r / "pkg" / "m.py").write_text("x = 1\n")
    (tmp_path / "r1" / "pkg" / "u.py").write_text("import pkg.m\n")
    monkeypatch.chdir(tmp_path)
    res = scan(["r1", "r2"])
    assert any("ambiguous module pkg.m" in w for w in res.warnings)
    assert ("IMPORTS", "r1/pkg/u.py", "pkg.m") in {(d.label, d.src, d.dst) for d in res.edges}


def test_excludes(tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir(); (tmp_path / ".venv" / "x.py").write_text("")
    (tmp_path / "gen").mkdir(); (tmp_path / "gen" / "y.py").write_text("")
    (tmp_path / "ok.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert [u.id for u in scan(["."], excludes=(".venv", "gen/**")).units] == ["ok.py"]


def test_terraform_refs_scoped_to_directory(tmp_path, monkeypatch):
    for d in ("infra/a", "infra/b"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "infra/a/main.tf").write_text('resource "aws_s3_bucket" "logs" {}\nresource "aws_s3_bucket_policy" "p" {\n  bucket = aws_s3_bucket.logs.id\n}\n')
    (tmp_path / "infra/b/main.tf").write_text('resource "aws_s3_bucket" "logs" {}\n')
    monkeypatch.chdir(tmp_path)
    e = {(d.label, d.src, d.dst) for d in scan(["."]).edges}
    assert ("REFERENCES", "infra/a/main.tf::aws_s3_bucket_policy.p", "infra/a/main.tf::aws_s3_bucket.logs") in e
    assert not any(dst == "infra/b/main.tf::aws_s3_bucket.logs" for lbl, _, dst in e if lbl == "REFERENCES")


def test_absolute_root_yields_relative_paths(tmp_path, monkeypatch):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("x = 1\n")
    monkeypatch.chdir(tmp_path)
    assert [u.id for u in scan([str(tmp_path / "pkg")]).units] == ["pkg/m.py"]


def test_exclude_path_prunes_directory(tmp_path, monkeypatch):
    (tmp_path / "tests" / "fixtures").mkdir(parents=True)
    (tmp_path / "tests" / "fixtures" / "f.py").write_text("")
    (tmp_path / "tests" / "t.py").write_text("")
    monkeypatch.chdir(tmp_path)
    assert [u.id for u in scan(["."], excludes=("tests/fixtures",)).units] == ["tests/t.py"]
    assert [u.id for u in scan(["."], excludes=("tests/fix*",)).units] == ["tests/t.py"]


def test_src_layout_warns_when_external_matches_scanned_module(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("import b\n")
    (tmp_path / "src" / "b.py").write_text("")
    monkeypatch.chdir(tmp_path)
    res = scan(["."])
    assert any("--src src" in w and "b" in w for w in res.warnings)


def test_unresolved_uses_alias_resolved_name(tmp_path, monkeypatch):
    (tmp_path / "m.py").write_text("import requests as r\nr.get()\n")
    monkeypatch.chdir(tmp_path)
    assert ("CALLS", "m.py", "unresolved::requests.get") in {(d.label, d.src, d.dst) for d in scan(["."]).edges}


def test_no_src_layout_hint_for_stdlib_shadowing(tmp_path, monkeypatch):
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "lib" / "time.py").write_text("")
    (tmp_path / "src" / "a.py").write_text("import time\n")
    monkeypatch.chdir(tmp_path)
    assert scan(["."]).warnings == []


def test_import_and_call_edges_carry_lines(repo):
    lines = {(d.label, d.src, d.dst): d.line for d in repo.edges}
    assert lines[("IMPORTS", "src/handlers/h.py", "logging")] == 1
    assert lines[("IMPORTS", "src/handlers/h.py", "src/db/q.py")] == 3
    assert lines[("CALLS", "src/handlers/h.py::src.handlers.h.leak", "src/db/q.py::src.db.q.run")] == 10


def test_scan_passes_ast_through(repo):
    assert len(repo.ast) > 100 and any(d.label == "CHILD" for d in repo.ast_edges)


def test_inherits_and_decorates_resolved(repo):
    e = {(d.label, d.src, d.dst) for d in repo.edges}
    assert ("INHERITS", "src/views.py::src.views.View", "src/views.py::src.views.Base") in e
    assert ("INHERITS", "src/views.py::src.views.View", "src/contracts/c.py::src.contracts.c.Contract") in e
    assert ("INHERITS", "src/views.py::src.views.Dyn", "unresolved::src.views.make_base()") in e
    assert ("DECORATES", "src/views.py::src.views.View.get", "unresolved::functools.lru_cache") in e   # alias resolved
    assert ("DECORATES", "src/views.py::src.views.handler", "src/views.py::src.views.route") in e
    assert ("DECORATES", "src/views.py::src.views.handler", "unresolved::functools.wraps") in e


def test_method_decorator_originates_from_method(repo):
    srcs = {d.src for d in repo.edges if d.label == "DECORATES" and d.dst == "unresolved::functools.lru_cache"}
    assert srcs == {"src/views.py::src.views.View.get"}


@pytest.fixture
def builtin_repo(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("def f(x):\n    print(len(x))\n")
    (tmp_path / "b.py").write_text("print('hi')\n")
    (tmp_path / "c.py").write_text("def print(*a):\n    pass\n\ndef g():\n    print(1)\n")
    monkeypatch.chdir(tmp_path)
    return scan(["."])


def test_builtin_calls_share_one_symbol_under_builtins(builtin_repo):
    calls = {(d.src, d.dst) for d in builtin_repo.edges if d.label == "CALLS"}
    assert ("a.py::a.f", "builtins::builtins.print") in calls
    assert ("a.py::a.f", "builtins::builtins.len") in calls
    assert ("b.py", "builtins::builtins.print") in calls
    sym = next(s for s in builtin_repo.symbols if s.id == "builtins::builtins.print")
    assert (sym.kind, sym.name, sym.qualified, sym.path) == ("builtin", "print", "builtins.print", "builtins")
    assert not any(s.qualified in ("a.print", "b.print", "a.len") for s in builtin_repo.symbols)


def test_module_defining_a_builtin_name_resolves_to_its_own(builtin_repo):
    calls = {(d.src, d.dst) for d in builtin_repo.edges if d.label == "CALLS"}
    assert ("c.py::c.g", "c.py::c.print") in calls
    assert ("c.py::c.g", "builtins::builtins.print") not in calls
