"""srcgraph command line: build, query, serve."""
import argparse
import json
import sys

from srcgraph import __version__
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES, scan
from srcgraph.selectors import SelectorError
from srcgraph.serve import make_server
from srcgraph.source import Source

DB = ".srcgraph/graph.lbug"


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

    common(sub.add_parser("build", help="build the graph database"))
    s = sub.add_parser("serve", help="build, then open the explorer in the browser")
    common(s)
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-build", action="store_true", help="serve the existing database")
    s.add_argument("--allow-query", action="store_true", help="enable POST /api/query (raw Cypher)")
    q = sub.add_parser("query", help="run Cypher against the last built graph")
    q.add_argument("--db", default=DB)
    q.add_argument("--format", choices=["text", "json"], default="text")
    q.add_argument("cypher")
    return p


def _run(args, out, err) -> int:
    if args.command == "query":
        with Graph.open(args.db) as graph:
            rows = graph.query(args.cypher)
        if args.format == "json":
            print(json.dumps(rows, indent=2, default=str), file=out)
        elif rows:
            cols = list(rows[0])
            print("\t".join(cols), file=out)
            for r in rows:
                print("\t".join(str(r[c]) for c in cols), file=out)
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
