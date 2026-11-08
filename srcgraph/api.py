"""The library front door: `srcgraph.load(root)` gives a Repo whose methods return the rows the CLI prints."""
import contextlib
import os
import sys

from srcgraph.cli import DB, PRESETS, build, domains as _domains, scope as _scope, trace as _trace
from srcgraph.graph import Graph
from srcgraph.scan import DEFAULT_EXCLUDES


class Repo:
    """An opened graph of one repository. Rows are lists of dicts; `pandas.DataFrame(rows)` takes them as is.
    The preset methods (dead, hubs, untested, callers(name), importers(module)) are attached below from PRESETS,
    so the CLI and the library cannot disagree."""

    def __init__(self, graph: Graph, db: str, root: str = ".", warnings: list[str] = ()):
        self.graph, self.db, self.root, self.warnings = graph, db, root, list(warnings)

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        return self.graph.query(cypher, params)

    def scope(self, glob: str, no_tests: bool = False, no_typing: bool = False) -> dict[str, list[dict]]:
        return _scope(self.graph, glob, no_tests, no_typing)[1]

    def domains(self, mapping: dict[str, str], no_typing: bool = False) -> dict[str, list[dict]]:
        return _domains(self.graph, mapping, no_typing)

    def trace(self, expr: str) -> list[dict]:
        with contextlib.chdir(self.root):   # symbol paths are relative to the root; the tracer imports from cwd
            return _trace(self.graph, expr, self.db)[1]

    def close(self) -> None:
        self.graph.close()

    def __enter__(self) -> "Repo":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _preset(name: str, cypher: str):
    if "$name" in cypher:
        def method(self, name_: str) -> list[dict]:
            return self.graph.query(cypher, {"name": name_, "prefix": name_ + "."})
    else:
        def method(self) -> list[dict]:
            return self.graph.query(cypher)
    method.__name__, method.__doc__ = name, PRESETS[name][0]
    return method


for _name, (_, _, _cypher) in PRESETS.items():
    setattr(Repo, _name, _preset(_name, _cypher))


def load(root: str = ".", db: str | None = None, src: list[str] | None = None,
         excludes: tuple[str, ...] = DEFAULT_EXCLUDES, rebuild: bool = False) -> Repo:
    """Open the graph of the repository at root, building it first when it is missing or rebuild is set.
    db defaults to <root>/.srcgraph/graph.lbug; src lists source roots relative to root (default the root itself)."""
    root = os.path.abspath(root)
    db = os.path.abspath(db) if db else os.path.join(root, DB)
    warnings: list[str] = []
    if rebuild or not os.path.exists(db):
        with contextlib.chdir(root):   # ponytail: process-wide cwd switch for the scan; paths in the graph are root-relative
            graph, warnings = build(src or ["."], excludes, db)
        for w in warnings:   # same line the CLI prints; the list is also kept on repo.warnings
            print(f"warning: {w}", file=sys.stderr)
    else:
        graph = Graph.open(db)
    return Repo(graph, db, root, warnings)
