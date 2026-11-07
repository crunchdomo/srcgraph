"""Acceptance against real repositories. Skipped unless SRCGRAPH_ACCEPT_REPO points at one or more checkouts:

    SRCGRAPH_ACCEPT_REPO=~/Documents/f1-race-replay:~/Documents/adr-as-code pytest tests/test_acceptance.py
"""
import os

import pytest

from srcgraph.cli import PRESETS
from srcgraph.graph import Graph
from srcgraph.scan import scan

REPOS = [os.path.expanduser(r) for r in os.environ.get("SRCGRAPH_ACCEPT_REPO", "").split(":") if r]
pytestmark = pytest.mark.skipif(not REPOS, reason="set SRCGRAPH_ACCEPT_REPO=<path>[:<path>...] to run")

# Share of CALLS edges landing on unresolved symbols. f1-race-replay on 2026-11-06: 0.951; with #1, #2 and #8: 0.377.
# Measured 2026-11-07: adr-as-code 0.25, srcgraph 0.36, kg-wiki 0.44, jobapps 0.49. What remains is mostly calls
# into external libraries and methods on untyped instances, which a static graph cannot place.
UNRESOLVED_CALL_CEILING = 0.5

README_QUERIES = {
    "top external imports": "MATCH (u:Unit)-[:IMPORTS]->(v:Unit {kind:'external'}) RETURN v.name, count(*) ORDER BY count(*) DESC LIMIT 5",
    "who imports a module": "MATCH (u:Unit)-[:IMPORTS]->(v:Unit) WHERE v.kind <> 'external' RETURN DISTINCT u.path LIMIT 5",
    "most-called symbols": "MATCH ()-[:CALLS]->(s:Symbol) WHERE s.kind <> 'unresolved' RETURN s.qualified, count(*) ORDER BY count(*) DESC LIMIT 5",
}


@pytest.fixture(scope="module", params=REPOS, ids=os.path.basename)
def graph(request, tmp_path_factory):
    db = str(tmp_path_factory.mktemp("accept") / "graph.lbug")
    cwd = os.getcwd()
    os.chdir(request.param)
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


@pytest.mark.parametrize("name", [n for n, (_, _, cypher) in PRESETS.items() if "$name" not in cypher])
def test_structure_preset_runs(graph, name):
    rows = graph.query(PRESETS[name][2])   # empty is a legitimate answer for dead and untested
    assert name != "hubs" or rows, "hubs returned no rows"
