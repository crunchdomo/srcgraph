"""Acceptance against a real repository. Skipped unless SRCGRAPH_ACCEPT_REPO points at a checkout:

    SRCGRAPH_ACCEPT_REPO=~/Documents/f1-race-replay pytest tests/test_acceptance.py
"""
import os

import pytest

from srcgraph.graph import Graph
from srcgraph.scan import scan

REPO = os.path.expanduser(os.environ.get("SRCGRAPH_ACCEPT_REPO", ""))
pytestmark = pytest.mark.skipif(not REPO, reason="set SRCGRAPH_ACCEPT_REPO=<path> to run")

# Share of CALLS edges landing on unresolved symbols. f1-race-replay on 2026-11-06: 0.951.
# Lower it as #1 (builtins), #2 (locals) and #8 (self.method) land.
UNRESOLVED_CALL_CEILING = 0.96

README_QUERIES = {
    "top external imports": "MATCH (u:Unit)-[:IMPORTS]->(v:Unit {kind:'external'}) RETURN v.name, count(*) ORDER BY count(*) DESC LIMIT 5",
    "who imports a module": "MATCH (u:Unit)-[:IMPORTS]->(v:Unit) WHERE v.kind <> 'external' RETURN DISTINCT u.path LIMIT 5",
    "most-called symbols": "MATCH ()-[:CALLS]->(s:Symbol) WHERE s.kind <> 'unresolved' RETURN s.qualified, count(*) ORDER BY count(*) DESC LIMIT 5",
}


@pytest.fixture(scope="module")
def graph(tmp_path_factory):
    db = str(tmp_path_factory.mktemp("accept") / "graph.lbug")
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        result = scan(["."])
    finally:
        os.chdir(cwd)
    g = Graph(db)
    g.load_code(result)
    yield g
    g.close()


@pytest.mark.parametrize("name", README_QUERIES)
def test_readme_query_returns_rows(graph, name):
    assert graph.query(README_QUERIES[name]), f"'{name}' returned no rows"


def test_unresolved_call_share_under_ceiling(graph):
    rows = graph.query("MATCH ()-[:CALLS]->(s:Symbol) RETURN s.kind = 'unresolved' AS unresolved, count(*) AS n")
    counts = {r["unresolved"]: r["n"] for r in rows}
    total = sum(counts.values())
    share = counts.get(True, 0) / total
    assert share < UNRESOLVED_CALL_CEILING, f"{counts.get(True, 0)}/{total} calls unresolved ({share:.3f})"
