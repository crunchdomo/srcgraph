"""srcgraph command line: build, query."""
import argparse
import json
import sys

from srcgraph import __version__
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES, scan
from srcgraph.selectors import SelectorError

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
    b = sub.add_parser("build", help="build the graph database")
    b.add_argument("--src", action="append", help="source root (repeatable; default .)")
    b.add_argument("--exclude", action="append", default=[], help="extra exclude name or glob (repeatable)")
    b.add_argument("--db", default=DB, help=f"graph database file (default {DB})")
    q = sub.add_parser("query", help="run Cypher against the last built graph")
    q.add_argument("--db", default=DB)
    q.add_argument("--format", choices=["text", "json"], default="text")
    q.add_argument("cypher")
    return p


def _run(args, out, err) -> int:
    if args.command == "query":
        graph = Graph.open(args.db)
        try:
            rows = graph.query(args.cypher)
        finally:
            graph.close()
        if args.format == "json":
            print(json.dumps(rows, indent=2, default=str), file=out)
        elif rows:
            cols = list(rows[0])
            print("\t".join(cols), file=out)
            for r in rows:
                print("\t".join(str(r[c]) for c in cols), file=out)
        return 0
    graph, warnings = build(args.src or ["."], DEFAULT_EXCLUDES + tuple(args.exclude), args.db)
    try:
        for w in warnings:
            print(f"warning: {w}", file=err)
        print(f"built {args.db}: {graph.count('Unit')} units, {graph.count('Symbol')} symbols, "
              f"{graph.count('Ast')} syntax nodes", file=out)
    finally:
        graph.close()
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
