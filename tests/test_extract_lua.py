from pathlib import Path

from srcgraph.extractors import EXTRACTORS
from srcgraph.extractors.lua import extract

ROOT = Path("tests/fixtures/luarepo/lua")


def _net():
    return extract("lua/app/net.lua", (ROOT / "app/net.lua").read_bytes(), "app.net")


def test_functions_become_symbols_named_by_the_module():
    e = _net()
    assert EXTRACTORS[".lua"] is extract
    unit = e.units[0]
    assert (unit.id, unit.name, unit.lang, unit.kind) == ("lua/app/net.lua", "app.net", "lua", "module")
    assert {(s.qualified, s.kind) for s in e.symbols} == {
        ("app.net.bump", "function"), ("app.net.send", "function"), ("app.net.flush", "method"),
        ("app.net.reset", "function"), ("app.net.unused", "function"), ("app.net.count", "function")}
    send = next(s for s in e.symbols if s.name == "send")
    assert (send.id, send.line, send.end_line) == ("lua/app/net.lua::app.net.send", 15, 20)
    assert ("CONTAINS", "lua/app/net.lua", send.id) in {(d.label, d.src, d.dst) for d in e.edges}


def _refs(e):
    return {(r.src_id.partition("::")[2] or r.src_id, r.name, r.relation, r.guard) for r in e.refs}


def test_require_is_an_import_with_the_local_name_it_binds():
    e = extract("lua/app/main.lua", (ROOT / "app/main.lua").read_bytes(), "app.main")
    assert {(i.module, i.alias, i.names, i.line) for i in e.imports} == {
        ("app.net", "net", (), 1), ("app.framing", None, (("pack", "pack"),), 2), ("dkjson", "json", (), 3)}
    assert all(i.unit_id == "lua/app/main.lua" and i.level == 0 for i in e.imports)


def test_calls_name_what_they_reach():
    assert _refs(_net()) == {
        ("app.net.send", "bump", "calls", "if@16"), ("app.net.send", "framing.pack", "calls", ""),
        ("app.net.flush", "ipairs", "calls", "for@23"), ("app.net.flush", "send", "calls", "for@23"),
        ("app.net.flush", "log", "calls", ""), ("app.net.flush", "string.format", "calls", ""),
        ("app.net.count", "bump", "calls", "")}   # queue.clear() is a call on a local value: dropped


def test_calls_from_the_file_itself_and_through_imported_names():
    e = extract("lua/app/main.lua", (ROOT / "app/main.lua").read_bytes(), "app.main")
    assert _refs(e) == {
        ("lua/app/main.lua", "net.send", "calls", ""), ("lua/app/main.lua", "pack", "calls", ""),
        ("lua/app/main.lua", "net.count", "calls", ""), ("lua/app/main.lua", "run", "calls", ""),
        ("app.main.run", "net.flush", "calls", ""), ("app.main.run", "json.encode", "calls", "")}


def test_other_tables_keep_their_name_and_closures_belong_to_their_function():
    e = extract("lua/app/framing.lua", (ROOT / "app/framing.lua").read_bytes(), "app.framing")
    assert {s.qualified for s in e.symbols} == {"app.framing.Codec.encode", "app.framing.pack", "app.framing.standalone"}
    assert _refs(e) == {("app.framing.Codec.encode", "tostring", "calls", ""),
                        ("app.framing.pack", "Codec.encode", "calls", ""),   # helper(frame) calls a local: dropped
                        ("app.framing.standalone", "pack", "calls", "")}


def test_a_function_passed_by_name_is_referenced():
    e = extract("a.lua", b"local function tick() end\nlocal function start(timer)\n  timer.every(1, tick)\nend\n", "a")
    assert _refs(e) == {("a.start", "tick", "names", "")}


def test_doc_is_the_comment_above():
    e = _net()
    docs = {s.name: s.doc for s in e.symbols}
    assert e.units[0].doc == "Sends frames to the server."
    assert docs["bump"] == "Counts one frame." and docs["send"] == "Sends one frame; the count goes up." and docs["flush"] == ""


def test_lua_syntax_tier():
    e = _net()
    assert {a.norm for a in e.ast if a.kind == "function_declaration"} == {"FunctionDef"}
    assert any(a.kind == "dot_index_expression" and a.norm == "Attribute" and a.text == "framing.pack" for a in e.ast)
    send = next(s for s in e.symbols if s.name == "send")
    assert next(d for d in e.edges if d.label == "DERIVED_FROM" and d.src == send.id).dst.endswith(":function_declaration")


def test_require_in_other_shapes_is_still_an_import():
    src = (b"local ok, trees = pcall(require, 'app/trees')\n"
           b"require('app/globe').clear()\n"
           b"pcall(require('app/globe').transform, 1)\n"
           b"local sites = require('/lua/ge/sites')\n"
           b"trees.plant()\n")
    e = extract("a.lua", src, "a")
    assert {(i.module, i.alias, i.line) for i in e.imports} == {
        ("app.trees", "trees", 1), ("app.globe", "@app/globe", 2), ("app.globe", "@app/globe", 3), ("lua.ge.sites", "sites", 4)}
    assert _refs(e) == {("a.lua", "@app/globe.clear", "calls", ""), ("a.lua", "@app/globe.transform", "names", ""),
                        ("a.lua", "pcall", "calls", ""), ("a.lua", "trees.plant", "calls", "")}


def test_self_means_the_table_the_function_sits_on_and_nothing_elsewhere():
    src = (b"local M = {}\nlocal Q = {}\nfunction Q.push() end\nfunction Q:drain() self.push() end\n"
           b"local function loose() self.push() end\n"
           b"local a, b = function() Q.push() end, function() loose() end\n"
           b"M.again = Q.push\nM.fmt = string.format\nreturn M\n")
    e = extract("q.lua", src, "q")
    assert _refs(e) == {("q.Q.drain", "Q.push", "calls", ""), ("q.a", "Q.push", "calls", ""), ("q.b", "loose", "calls", ""),
                        ("q.again", "Q.push", "calls", "")}   # loose() has no self; string.format is not this file's


def test_a_local_value_is_not_the_exported_function_of_the_same_name():
    src = (b"local M = {}\nlocal state = 'idle'\nlocal function reset() state = 'idle' end\n"
           b"function M.state() return state end\n"
           b"function M.ready() reset() return state == 'up', M.state() end\n"
           b"M.reset = reset\nreturn M\n")
    e = extract("n.lua", src, "n")
    assert _refs(e) == {("n.ready", "reset", "calls", ""), ("n.ready", "state", "calls", "")}   # M.state(), not the local


def test_a_field_on_the_export_is_not_the_local_function_of_the_same_name():
    src = (b"local M = {}\nlocal function gps()\n  if not M.gps then M.gps = load() end\n  return M.gps\nend\n"
           b"function M.find() return gps() end\nreturn M\n")
    assert _refs(extract("f.lua", src, "f")) == {("f.gps", "load", "calls", "if@3"), ("f.find", "gps", "calls", "")}
