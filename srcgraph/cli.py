"""srcgraph command line: build, query, serve, the presets importers and callers, and scope."""
import argparse
import json
import sys

from srcgraph import __version__
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES, scan
from srcgraph.selectors import SelectorError, compile_selector
from srcgraph.serve import make_server
from srcgraph.source import Source

DB = ".srcgraph/graph.lbug"
# Presets: the common questions without Cypher. name -> (help, empty message, cypher over $name and $prefix)
PRESETS = {
    "importers": ("units that import a module or any of its submodules", "no importers of {}",
                  "MATCH (u:Unit)-[i:IMPORTS]->(v:Unit) WHERE v.name = $name OR v.name STARTS WITH $prefix "
                  "RETURN u.path AS importer, i.line AS line, v.name AS module ORDER BY importer, line"),
    "callers": ("symbols and units that call a symbol, by name or qualified name", "no callers of {}",
                "MATCH (c)-[r:CALLS]->(s:Symbol) WHERE s.name = $name OR s.qualified = $name "
                "RETURN coalesce(c.qualified, c.path) AS caller, r.line AS line, s.qualified AS target ORDER BY caller, line"),
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

    def ro(sp, positional, help_=None):
        sp.add_argument("--db", default=DB)
        sp.add_argument("--format", choices=["text", "json"], default="text")
        sp.add_argument(positional, help=help_)

    common(sub.add_parser("build", help="build the graph database"))
    s = sub.add_parser("serve", help="build, then open the explorer in the browser")
    common(s)
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-build", action="store_true", help="serve the existing database")
    s.add_argument("--allow-query", action="store_true", help="enable POST /api/query (raw Cypher)")
    ro(sub.add_parser("query", help="run Cypher against the last built graph"), "cypher")
    for name, (help_, _, _) in PRESETS.items():
        ro(sub.add_parser(name, help=help_), "name")
    ro(sub.add_parser("scope", help="one compartment: its doc line, internal wiring, outbound and inbound wires"),
       "glob", "path glob for the compartment, e.g. 'src/gui/**'")
    return p


def scope(graph: Graph, glob: str) -> tuple[int, dict[str, list[dict]]]:
    """What happens inside one compartment and what crosses its boundary. Returns (matched node count, sections)."""
    a, pa = compile_selector({"path": glob}, "a", "a")
    b, pb = compile_selector({"path": glob}, "b", "b")
    params = {**pa, **pb}
    matched = graph.query(f"MATCH (a) WHERE {a} RETURN count(*) AS c", params)[0]["c"]

    def edges(where: str) -> list[dict]:
        # unresolved and builtin targets are not wires that land anywhere worth drawing
        return graph.query(f"MATCH (a)-[r:CALLS|REFERENCES|INHERITS|IMPORTS]->(b) WHERE {where} AND NOT b.kind IN ['unresolved', 'builtin'] "
                           f"RETURN coalesce(a.qualified, a.path) AS source, label(r) AS label, "
                           f"coalesce(b.qualified, b.name) AS target, r.line AS line ORDER BY source, line, target", params)
    return matched, {
        "doc": graph.query(f"MATCH (a:Unit) WHERE {a} AND a.doc <> '' RETURN a.path AS path, a.doc AS doc ORDER BY path", params),
        "internal": edges(f"({a}) AND ({b})"),
        "outbound": edges(f"({a}) AND NOT ({b})"),
        "inbound": edges(f"NOT ({a}) AND ({b})"),
    }


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
            matched, sections = scope(graph, args.glob)
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
    if args.command in PRESETS:
        _, empty, cypher = PRESETS[args.command]
        with Graph.open(args.db) as graph:
            rows = graph.query(cypher, {"name": args.name, "prefix": args.name + "."})
        if not rows:
            print(empty.format(args.name), file=err)
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
            return serve(graph, Source((args.src or ["."])[0]), args.port, args.allow_query, out)
        print(f"built {args.db}: {graph.count('Unit')} units, {graph.count('Symbol')} symbols, "
              f"{graph.count('Ast')} syntax nodes", file=out)
    return 0


def serve(graph: Graph, source: Source, port: int, allow_query: bool, out, routes: dict | None = None) -> int:
    """Serve the explorer until Ctrl-C. adrgraph calls this with its overlay routes."""
    srv = make_server(graph, source, port, routes, allow_query)
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
