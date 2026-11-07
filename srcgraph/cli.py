"""srcgraph command line: build, query, serve, the presets importers and callers, scope, and trace."""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

from srcgraph import __version__, tracer
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES, scan
from srcgraph.selectors import SelectorError, compile_selector
from srcgraph.serve import make_server
from srcgraph.source import Source

DB = ".srcgraph/graph.lbug"
# Presets: the common questions without Cypher. name -> (help, empty message, cypher). A cypher that mentions
# $name takes a positional argument (also bound as $prefix = name + "."); the structure presets take none.
WIRES = "CALLS|IMPORTS|REFERENCES|INHERITS"
PRESETS = {
    "importers": ("units that import a module or any of its submodules", "no importers of {}",
                  "MATCH (u:Unit)-[i:IMPORTS]->(v:Unit) WHERE v.name = $name OR v.name STARTS WITH $prefix "
                  "RETURN u.path AS importer, i.line AS line, v.name AS module ORDER BY importer, line"),
    "callers": ("symbols and units that call a symbol, by name or qualified name", "no callers of {}",
                "MATCH (c)-[r:CALLS]->(s:Symbol) WHERE s.name = $name OR s.qualified = $name "
                "RETURN coalesce(c.qualified, c.path) AS caller, r.line AS line, s.qualified AS target, r.guard AS guard ORDER BY caller, line"),
    "dead": ("symbols nothing calls, references, inherits or decorates, and that are not entry points (candidates: "
             "getattr and registries are invisible)", "nothing is dead",
             # a method whose name is accessed on anything anywhere (an x.name attribute in the syntax tier) is not
             # dead: the graph has no types, so that is the best it can say
             "MATCH (u:Unit)-[:CONTAINS*1..3]->(s:Symbol) WHERE NOT u.test AND s.kind IN ['function', 'method', 'class'] "
             "AND NOT s.entry AND NOT s.name STARTS WITH '__' "
             "OPTIONAL MATCH ()-[r:CALLS|REFERENCES|INHERITS|DECORATES]->(s) WITH s, count(r) AS n WHERE n = 0 "
             # decorated: a framework registered it (routes, tasks, signals); DECORATES points from the symbol to the decorator
             "OPTIONAL MATCH (s)-[d:DECORATES]->() WITH s, count(d) AS deco WHERE deco = 0 "
             # a method on a subclass of something external overrides a hook the framework calls (paintEvent, handle_data)
             # ponytail: every method on such a class is skipped; telling overrides apart needs the base's method list
             "OPTIONAL MATCH (c:Symbol)-[:CONTAINS]->(s) WHERE s.kind = 'method' "
             "OPTIONAL MATCH (c)-[:INHERITS]->(b) WHERE b.kind IN ['unresolved', 'external'] WITH s, count(b) AS ext WHERE ext = 0 "
             "OPTIONAL MATCH (x:Ast {norm: 'Attribute'}) WHERE s.kind = 'method' AND ends_with(x.text, concat('.', s.name)) "
             "WITH s, count(x) AS m WHERE m = 0 "
             "RETURN s.qualified AS symbol, s.path AS path, s.line AS line ORDER BY path, line"),
    "hubs": ("symbols and units with the most distinct neighbours in and out: what you must not break, and what does too much",
             "no wires at all",
             f"MATCH (n:Symbol:Unit) WHERE NOT n.kind IN ['unresolved', 'builtin', 'external'] "
             f"OPTIONAL MATCH (a)-[:{WIRES}]->(n) WITH n, count(DISTINCT a) AS fan_in "
             f"OPTIONAL MATCH (n)-[:{WIRES}]->(b) WHERE NOT b.kind IN ['unresolved', 'builtin'] "
             "WITH n, fan_in, count(DISTINCT b) AS fan_out WHERE fan_in + fan_out > 0 "
             "RETURN coalesce(n.qualified, n.path) AS node, fan_in, fan_out ORDER BY fan_in + fan_out DESC, node LIMIT 30"),
    "untested": ("symbols no test unit reaches through calls (up to six hops)", "everything is reached from a test",
                 # ponytail: 6-hop expansion from every test unit; precompute a reached set if a big repo makes this slow
                 "MATCH (u:Unit)-[:CONTAINS*1..3]->(s:Symbol) WHERE NOT u.test AND s.kind IN ['function', 'method', 'class'] "
                 "OPTIONAL MATCH (t:Unit {test: true})-[:CONTAINS*1..2]->(:Symbol)-[:CALLS*1..6]->(s) WITH s, count(t) AS n WHERE n = 0 "
                 "RETURN s.qualified AS symbol, s.path AS path, s.line AS line ORDER BY path, line"),
}


def build(src_roots: list[str], excludes: tuple[str, ...], db_path: str) -> tuple[Graph, list[str]]:
    """Scan the roots and load a fresh graph; returns the graph and the scan warnings."""
    result = scan(src_roots, excludes)
    graph = Graph(db_path)
    graph.load_code(result)
    return graph, result.warnings


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="srcgraph", description="Source code in, a queryable two-tier knowledge graph out.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp):
        sp.add_argument("--src", action="append", help="source root (repeatable; default .)")
        sp.add_argument("--exclude", action="append", default=[], help="extra exclude name or glob (repeatable)")
        sp.add_argument("--db", default=DB, help=f"graph database file (default {DB})")

    def ro(sp, positional=None, help_=None):
        sp.add_argument("--db", default=DB)
        sp.add_argument("--format", choices=["text", "json"], default="text")
        if positional:
            sp.add_argument(positional, help=help_)

    common(sub.add_parser("build", help="build the graph database"))
    s = sub.add_parser("serve", help="build, then open the explorer in the browser")
    common(s)
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-build", action="store_true", help="serve the existing database")
    s.add_argument("--allow-query", action="store_true", help="enable POST /api/query (raw Cypher)")
    ro(sub.add_parser("query", help="run Cypher against the last built graph"), "cypher")
    for name, (help_, _, cypher) in PRESETS.items():
        ro(sub.add_parser(name, help=help_), "name" if "$name" in cypher else None)
    sp = sub.add_parser("scope", help="one compartment: its files with doc lines, internal wiring, outbound and inbound wires")
    ro(sp, "glob", "path glob for the compartment, e.g. 'src/gui/**'")
    sp.add_argument("--no-tests", action="store_true", help="hide wires that touch a test unit")
    ro(sub.add_parser("trace", help="run an expression in the interpreter on PATH and print the calls it makes through the graph"),
       "expr", "python expression, e.g. 'pkg.mod.f(1)', or a pytest node id like tests/test_x.py::test_y; activate the target's venv first")
    return p


def scope(graph: Graph, glob: str, no_tests: bool = False) -> tuple[int, dict[str, list[dict]]]:
    """What happens inside one compartment and what crosses its boundary. Returns (matched node count, sections)."""
    a, pa = compile_selector({"path": glob}, "a", "a")
    b, pb = compile_selector({"path": glob}, "b", "b")
    params = {**pa, **pb}
    matched = graph.query(f"MATCH (a) WHERE {a} RETURN count(*) AS c", params)[0]["c"]
    # a symbol's unit is the Unit with its path; --no-tests drops wires with a test unit at either end
    tests = ", (ua:Unit), (ub:Unit)" if no_tests else ""
    not_tests = " AND ua.path = a.path AND ub.path = b.path AND NOT ua.test AND NOT ub.test" if no_tests else ""

    def edges(where: str) -> list[dict]:
        # unresolved and builtin targets are not wires that land anywhere worth drawing
        return graph.query(f"MATCH (a)-[r:CALLS|REFERENCES|INHERITS|IMPORTS]->(b){tests} WHERE {where} "
                           f"AND NOT b.kind IN ['unresolved', 'builtin']{not_tests} "
                           f"RETURN coalesce(a.qualified, a.path) AS source, label(r) AS label, "
                           f"coalesce(b.qualified, b.name) AS target, r.line AS line, coalesce(r.guard, '') AS guard "
                           f"ORDER BY source, line, target", params)
    return matched, {
        "doc": graph.query(f"MATCH (a:Unit) WHERE {a} RETURN a.path AS path, a.doc AS doc ORDER BY path", params),
        "internal": edges(f"({a}) AND ({b})"),
        "outbound": edges(f"({a}) AND NOT ({b})"),
        "inbound": edges(f"NOT ({a}) AND ({b})"),
    }


def traces_dir(db: str) -> str:
    return os.path.join(os.path.dirname(db) or ".", "traces")


def trace(graph: Graph, expr: str, db: str) -> tuple[int, list[dict]]:
    """Run expr under srcgraph/tracer.py in the `python` on PATH; returns (exit code, call/return/exc events)
    and saves them under <db dir>/traces/ for the explorer. Symbol paths are relative to the build's cwd, so run
    this where you ran build."""
    table: dict[str, dict[str, list]] = {}
    for r in graph.query("MATCH (s:Symbol) WHERE s.kind IN ['function', 'method'] "
                         "RETURN s.path AS path, s.name AS name, s.line AS line, s.qualified AS q ORDER BY line"):
        table.setdefault(os.path.abspath(r["path"]), {}).setdefault(r["name"], []).append([r["line"], r["q"], f"{r['path']}::{r['q']}"])
    with tempfile.TemporaryDirectory() as d:
        tp, ep = os.path.join(d, "table.json"), os.path.join(d, "events.jsonl")
        json.dump(table, open(tp, "w"))
        proc = subprocess.run([shutil.which("python") or sys.executable, tracer.__file__, tp, ep, expr])
        events = [json.loads(l) for l in open(ep)] if os.path.exists(ep) else []
    os.makedirs(traces_dir(db), exist_ok=True)
    now = datetime.now()
    with open(os.path.join(traces_dir(db), now.strftime("%Y%m%d-%H%M%S-%f") + ".json"), "w") as f:
        json.dump({"input": expr, "at": now.isoformat(timespec="seconds"), "events": events}, f)
    return proc.returncode, events


def render(events: list[dict]) -> list[str]:
    """Trace events as an indented tree. Runs of 3+ sibling calls to the same function collapse to first, × N, last."""
    root: dict = {"kids": []}
    stack = [root]   # stack[d] is the open call at depth d - 1; stack[0] the root
    for ev in events:
        d = ev["depth"]
        del stack[d + 1:]
        if ev["ev"] == "call":
            node = {**ev, "kids": []}
            stack[-1]["kids"].append(node)
            stack.append(node)
        else:   # return or exc belongs to the call one level up
            stack[d].update(ev)

    def ms(node) -> float:
        return node.get("ms", 0.0)

    def line(node, pad) -> list[str]:
        out = [f"{pad}{node['sym']}({', '.join(f'{k}={v}' for k, v in node['args'].items())})", *lines(node["kids"], pad + "  ")]
        if "value" in node:
            out.append(f"{pad}  = {node['value']}  ({ms(node):.1f} ms)")
        elif "text" in node:
            out.append(f"{pad}  ! {node['text']}")
        return out

    def lines(kids, pad) -> list[str]:
        out, i = [], 0
        while i < len(kids):
            j = i
            while j < len(kids) and kids[j]["sym"] == kids[i]["sym"]:
                j += 1
            if j - i >= 3:
                out += line(kids[i], pad) + [f"{pad}× {j - i} calls  ({sum(map(ms, kids[i:j])):.1f} ms)"] + line(kids[j - 1], pad)
            else:
                for k in kids[i:j]:
                    out += line(k, pad)
            i = j
        return out

    out = lines(root["kids"], "")
    if "value" in root:
        out.append(f"= {root['value']}  ({ms(root):.1f} ms)")
    elif "text" in root:
        out.append(f"! {root['text']}")
    return out


def _print(rows, fmt: str, out) -> int:
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str), file=out)
    elif rows:
        cols = list(rows[0])
        print("\t".join(cols), file=out)
        for r in rows:
            print("\t".join(str(r[c]) for c in cols), file=out)
    return 0


def _run(args, out, err) -> int:
    if args.command == "query":
        with Graph.open(args.db) as graph:
            rows = graph.query(args.cypher)
        _print(rows, args.format, out)
        return 0
    if args.command == "scope":
        with Graph.open(args.db) as graph:
            matched, sections = scope(graph, args.glob, args.no_tests)
        if not matched:
            print(f"nothing matches {args.glob}", file=err)
            return 1
        if args.format == "json":
            return _print(sections, "json", out)
        for d in sections["doc"]:
            print(f"{d['path']}\t{d['doc']}", file=out)
        for name in ("internal", "outbound", "inbound"):
            print(f"== {name} ({len(sections[name])})", file=out)
            _print(sections[name], "text", out)
        return 0
    if args.command == "trace":
        with Graph.open(args.db) as graph:
            code, events = trace(graph, args.expr, args.db)
        if args.format == "json":
            _print(events, "json", out)
            return code
        for line in render(events):
            print(line, file=out)
        return code
    if args.command in PRESETS:
        from srcgraph.api import Repo   # the same object library users get, so the two cannot drift
        empty, name = PRESETS[args.command][1], getattr(args, "name", "")
        with Repo(Graph.open(args.db), args.db) as repo:
            if args.command == "untested" and not repo.query("MATCH (u:Unit {test: true}) RETURN u.id LIMIT 1"):
                print("no test units: nothing reaches anything", file=err)
                return 1
            rows = getattr(repo, args.command)(name) if name else getattr(repo, args.command)()
        if not rows:
            print(empty.format(name), file=err)
            return 1
        _print(rows, args.format, out)
        return 0
    if args.command == "serve" and args.no_build:
        graph, warnings = Graph.open(args.db, read_only=True), []
    else:
        graph, warnings = build(args.src or ["."], DEFAULT_EXCLUDES + tuple(args.exclude), args.db)
    with graph:
        for w in warnings:
            print(f"warning: {w}", file=err)
        if args.command == "serve":
            return serve(graph, Source((args.src or ["."])[0]), args.port, args.allow_query, out, traces=traces_dir(args.db))
        print(f"built {args.db}: {graph.count('Unit')} units, {graph.count('Symbol')} symbols, "
              f"{graph.count('Ast')} syntax nodes", file=out)
    return 0


def serve(graph: Graph, source: Source, port: int, allow_query: bool, out, routes: dict | None = None,
          traces: str | None = None) -> int:
    """Serve the explorer until Ctrl-C. adrgraph calls this with its overlay routes."""
    srv = make_server(graph, source, port, routes, allow_query, traces=traces)
    print(f"explorer at http://127.0.0.1:{srv.server_address[1]}/  (Ctrl-C to stop)", file=out, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, sys.stdout, sys.stderr)
    except (SelectorError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
