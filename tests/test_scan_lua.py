import pytest

from srcgraph.scan import module_name, scan

NET, FRAMING, MAIN = "lua/app/net.lua", "lua/app/framing.lua", "lua/app/main.lua"


@pytest.fixture
def repo(monkeypatch):
    monkeypatch.chdir("tests/fixtures/luarepo")
    return scan(["."])


def _edges(res):
    return {(d.label, d.src, d.dst) for d in res.edges}


def test_lua_module_name():
    assert module_name(".", "lua/app/net.lua") == "lua.app.net" and module_name(".", "pkg/init.lua") == "pkg"


def test_require_resolves_by_path_suffix_when_the_package_root_is_not_a_source_root(repo):
    e = _edges(repo)
    assert ("IMPORTS", MAIN, NET) in e and ("IMPORTS", MAIN, FRAMING) in e and ("IMPORTS", NET, FRAMING) in e
    assert ("IMPORTS", "tests/test_net.lua", NET) in e
    assert ("IMPORTS", MAIN, "lua:dkjson") in e
    ext = next(u for u in repo.units if u.id == "lua:dkjson")
    assert (ext.kind, ext.lang, ext.name) == ("external", "lua", "dkjson")
    assert not repo.warnings


def test_require_resolves_by_name_under_its_source_root(monkeypatch):
    monkeypatch.chdir("tests/fixtures/luarepo")
    res = scan(["lua", "tests"])
    assert next(u for u in res.units if u.id == NET).name == "app.net"
    assert ("IMPORTS", MAIN, NET) in _edges(res)
    assert ("CALLS", MAIN, f"{NET}::app.net.send") in _edges(res)


def test_calls_cross_files_through_the_required_name(repo):
    e = _edges(repo)
    assert ("CALLS", MAIN, f"{NET}::lua.app.net.send") in e
    assert ("CALLS", MAIN, f"{FRAMING}::lua.app.framing.pack") in e          # local pack = require(...).pack
    assert ("CALLS", f"{MAIN}::lua.app.main.run", f"{NET}::lua.app.net.flush") in e
    assert ("CALLS", f"{NET}::lua.app.net.send", f"{FRAMING}::lua.app.framing.pack") in e
    assert ("CALLS", MAIN, f"{NET}::lua.app.net.count") in e                 # an export under another name ...
    assert ("CALLS", f"{NET}::lua.app.net.count", f"{NET}::lua.app.net.bump") in e   # ... reaches the function


def test_calls_inside_a_file(repo):
    e = _edges(repo)
    assert ("CALLS", f"{NET}::lua.app.net.send", f"{NET}::lua.app.net.bump") in e
    assert ("CALLS", f"{NET}::lua.app.net.flush", f"{NET}::lua.app.net.send") in e
    assert ("CALLS", f"{FRAMING}::lua.app.framing.pack", f"{FRAMING}::lua.app.framing.Codec.encode") in e
    assert ("CALLS", MAIN, f"{MAIN}::lua.app.main.run") in e
    assert not any(lbl == "CALLS" and dst.endswith("unused") for lbl, _, dst in e)


def test_standard_library_is_builtin_and_unknown_globals_are_unresolved(repo):
    e = _edges(repo)
    flush = f"{NET}::lua.app.net.flush"
    assert ("CALLS", flush, "builtins::lua.ipairs") in e and ("CALLS", flush, "builtins::lua.string.format") in e
    assert ("CALLS", flush, "unresolved::lua:log") in e
    assert ("CALLS", f"{MAIN}::lua.app.main.run", "unresolved::lua:dkjson.encode") in e
    kinds = {s.id: (s.kind, s.lang) for s in repo.symbols}
    assert kinds["builtins::lua.ipairs"] == ("builtin", "lua") and kinds["unresolved::lua:log"] == ("unresolved", "lua")
    assert "builtins::builtins.print" not in kinds   # Lua's print is not Python's


def test_a_test_file_makes_what_it_calls_an_entry_point(repo):
    entry = {s.name for s in repo.symbols if s.entry}
    assert "reset" in entry and "unused" not in entry
    assert next(u for u in repo.units if u.id == "tests/test_net.lua").test


def test_a_function_passed_by_name_is_a_reference_and_an_unknown_name_is_nothing(tmp_path, monkeypatch):
    (tmp_path / "a.lua").write_text("local b = require('b')\nlocal function tick() end\n"
                                    "local function start(timer)\n  timer.every(1, tick)\n  timer.every(2, b.tock, b.missing)\nend\n")
    (tmp_path / "b.lua").write_text("local M = {}\nfunction M.tock() end\nreturn M\n")
    monkeypatch.chdir(tmp_path)
    res = scan(["."])
    e = _edges(res)
    assert ("REFERENCES", "a.lua::a.start", "a.lua::a.tick") in e and ("REFERENCES", "a.lua::a.start", "b.lua::b.tock") in e
    assert not any("missing" in s.id for s in res.symbols)


def test_a_require_two_files_could_answer_stays_external(tmp_path, monkeypatch):
    for d in ("x", "y"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "util.lua").write_text("local M = {}\nfunction M.f() end\nreturn M\n")
    (tmp_path / "main.lua").write_text("local util = require('util')\nutil.f()\n")
    monkeypatch.chdir(tmp_path)
    res = scan(["."])
    e = _edges(res)
    assert ("IMPORTS", "main.lua", "lua:util") in e and ("CALLS", "main.lua", "unresolved::lua:util.f") in e
    assert res.warnings == ["ambiguous Lua module util: x/util.lua, y/util.lua"]


def test_a_call_on_an_inline_require_reaches_the_function(tmp_path, monkeypatch):
    (tmp_path / "a.lua").write_text("require('b').tock()\nlocal ok, c = pcall(require, 'b')\nc.tock()\n")
    (tmp_path / "b.lua").write_text("local M = {}\nfunction M.tock() end\nreturn M\n")
    monkeypatch.chdir(tmp_path)
    res = scan(["."])
    calls = [(d.src, d.dst, d.line) for d in res.edges if d.label == "CALLS" and d.dst == "b.lua::b.tock"]
    assert calls == [("a.lua", "b.lua::b.tock", 1), ("a.lua", "b.lua::b.tock", 3)]
    assert not any(s.kind == "unresolved" for s in res.symbols)


def test_a_lua_file_beside_a_python_module_of_the_same_name_leaves_python_imports_alone(tmp_path, monkeypatch):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "rec.py").write_text("def go():\n    pass\n")
    (tmp_path / "tools" / "rec.lua").write_text("local M = {}\nfunction M.go() end\nreturn M\n")
    (tmp_path / "main.py").write_text("from tools import rec\nrec.go()\n")
    (tmp_path / "main.lua").write_text("local rec = require('tools/rec')\nrec.go()\n")
    monkeypatch.chdir(tmp_path)
    res = scan(["."])
    e = _edges(res)
    assert not res.warnings
    assert ("CALLS", "main.py", "tools/rec.py::tools.rec.go") in e and ("CALLS", "main.lua", "tools/rec.lua::tools.rec.go") in e
    assert ("IMPORTS", "main.py", "tools/rec.py") in e and ("IMPORTS", "main.lua", "tools/rec.lua") in e


def _repo(tmp_path, monkeypatch, files, roots=(".",)):
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.chdir(tmp_path)
    return scan(list(roots))


MOD = "local M = {}\nfunction M.f() end\nreturn M\n"


def test_a_global_is_not_a_sibling_module_nobody_required(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {"app/init.lua": "local M = {}\nfunction M.start() util.f() end\nreturn M\n", "app/util.lua": MOD})
    assert ("CALLS", "app/init.lua::app.start", "unresolved::lua:util.f") in _edges(res)
    assert not any(lbl == "CALLS" and dst.endswith("app.util.f") for lbl, _, dst in _edges(res))


def test_two_modules_of_one_name_get_no_calls_and_a_warning(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {"lua/helpers.lua": MOD, "spec/helpers.lua": MOD,
                                        "lua/main.lua": "local h = require('helpers')\nh.f()\n"}, roots=("lua", "spec"))
    assert ("CALLS", "lua/main.lua", "unresolved::lua:helpers.f") in _edges(res)
    assert res.warnings == ["ambiguous Lua module helpers: lua/helpers.lua, spec/helpers.lua"]


def test_each_function_reaches_the_module_its_own_local_names(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {
        "a.lua": MOD, "b.lua": MOD,
        "main.lua": "local function one()\n  local m = require('a')\n  m.f()\nend\n"
                    "local function two()\n  local m = require('b')\n  m.f()\n  local a = {}\n  a.f()\nend\n"})
    calls = {(src, dst) for lbl, src, dst in _edges(res) if lbl == "CALLS"}
    assert calls == {("main.lua::main.one", "a.lua::a.f"), ("main.lua::main.two", "b.lua::b.f")}


def test_lua_and_python_do_not_share_a_library_or_an_unknown_name(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {"a.lua": "local socket = require('socket')\nsocket.connect()\n",
                                        "b.py": "import socket\nsocket.connect()\n"})
    e = _edges(res)
    assert ("IMPORTS", "b.py", "socket") in e and ("IMPORTS", "a.lua", "lua:socket") in e
    assert ("CALLS", "b.py", "unresolved::socket.connect") in e and ("CALLS", "a.lua", "unresolved::lua:socket.connect") in e
    assert {u.id: u.lang for u in res.units if u.kind == "external"} == {"socket": "python", "lua:socket": "lua"}
    assert {s.id: s.lang for s in res.symbols if s.kind == "unresolved"} == {
        "unresolved::socket.connect": "python", "unresolved::lua:socket.connect": "lua"}


def test_a_python_package_does_not_contain_a_lua_module(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {"src/app/__init__.py": "", "src/app/core.py": "def f():\n    pass\n",
                                        "lua/app/net.lua": MOD}, roots=("src", "lua"))
    contained = {dst for lbl, src, dst in _edges(res) if lbl == "CONTAINS" and src == "src/app/__init__.py"}
    assert contained == {"src/app/core.py"}


def test_requiring_a_packages_init_by_name_finds_the_package(tmp_path, monkeypatch):
    res = _repo(tmp_path, monkeypatch, {"app/init.lua": MOD, "main.lua": "local app = require('app.init')\napp.f()\n"})
    assert ("IMPORTS", "main.lua", "app/init.lua") in _edges(res) and ("CALLS", "main.lua", "app/init.lua::app.f") in _edges(res)
