"""srcgraph command line: build, query, the presets importers and callers, and scope."""
import argparse
import json
import sys

from srcgraph import __version__
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES, scan
from srcgraph.selectors import SelectorError, compile_selector

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
    b = sub.add_parser("build", help="build the graph database")
    b.add_argument("--src", action="append", help="source root (repeatable; default .)")
    b.add_argument("--exclude", action="append", default=[], help="extra exclude name or glob (repeatable)")
    b.add_argument("--db", default=DB, help=f"graph database file (default {DB})")
    q = sub.add_parser("query", help="run Cypher against the last built graph")
    q.add_argument("--db", default=DB)
    q.add_argument("--format", choices=["text", "json"], default="text")
    q.add_argument("cypher")
    for name, (help_, _, _) in PRESETS.items():
        s = sub.add_parser(name, help=help_)
        s.add_argument("--db", default=DB)
        s.add_argument("--format", choices=["text", "json"], default="text")
        s.add_argument("name")
    sc = sub.add_parser("scope", help="one compartment: its doc line, internal wiring, outbound and inbound wires")
    sc.add_argument("--db", default=DB)
    sc.add_argument("--format", choices=["text", "json"], default="text")
    sc.add_argument("glob", help="path glob for the compartment, e.g. 'src/gui/**'")
    return p


SCOPE_EDGES = "CALLS|REFERENCES|INHERITS|IMPORTS"


def scope(graph: Graph, glob: str) -> tuple[int, dict[str, list[dict]]]:
    """What happens inside one compartment and what crosses its boundary. Returns (matched node count, sections)."""
    a, pa = compile_selector({"path": glob}, "a", "a")
    b, pb = compile_selector({"path": glob}, "b", "b")
    params = {**pa, **pb}
    matched = graph.query(f"MATCH (a) WHERE {a} RETURN count(*) AS c", params)[0]["c"]

    def edges(where: str) -> list[dict]:
        # unresolved and builtin targets are not wires that land anywhere worth drawing
        return graph.query(f"MATCH (a)-[r:{SCOPE_EDGES}]->(b) WHERE {where} AND NOT b.kind IN ['unresolved', 'builtin'] "
                           f"RETURN coalesce(a.qualified, a.path) AS source, label(r) AS label, "
                           f"coalesce(b.qualified, b.name) AS target, r.line AS line ORDER BY source, line, target", params)
    return matched, {
        "doc": graph.query(f"MATCH (a:Unit) WHERE {a} AND a.doc <> '' RETURN a.path AS path, a.doc AS doc ORDER BY path", params),
        "internal": edges(f"({a}) AND ({b})"),
        "outbound": edges(f"({a}) AND NOT ({b})"),
        "inbound": edges(f"NOT ({a}) AND ({b})"),
    }


def _print(rows: list[dict], fmt: str, out) -> None:
    if fmt == "json":
        print(json.dumps(rows, indent=2, default=str), file=out)
    elif rows:
        cols = list(rows[0])
        print("\t".join(cols), file=out)
        for r in rows:
            print("\t".join(str(r[c]) for c in cols), file=out)


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
            print(json.dumps(sections, indent=2, default=str), file=out)
            return 0
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
    graph, warnings = build(args.src or ["."], DEFAULT_EXCLUDES + tuple(args.exclude), args.db)
    with graph:
        for w in warnings:
            print(f"warning: {w}", file=err)
        print(f"built {args.db}: {graph.count('Unit')} units, {graph.count('Symbol')} symbols, "
              f"{graph.count('Ast')} syntax nodes", file=out)
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
