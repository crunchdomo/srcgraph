import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from srcgraph.cli import build
from srcgraph.serve import make_server
from srcgraph.source import Source


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    cwd = os.getcwd()
    os.chdir("tests/fixtures/pyrepo")   # ids are paths relative to the working directory
    graph, _ = build(["."], (), str(tmp_path_factory.mktemp("db") / "g.lbug"))
    srv = make_server(graph, Source("."), port=0, routes={"/api/hello": lambda p: {"hi": p.get("x")}})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    graph.close()
    os.chdir(cwd)


def get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, json.loads(r.read()) if r.headers.get_content_type() == "application/json" else r.read().decode()


def status(base, path, data=None):
    try:
        req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET")
        with urllib.request.urlopen(req) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_index_is_the_explorer_page(server):
    _, body = get(server, "/")
    assert "<title>" in body and "d3" in body


def test_search_finds_units_and_symbols(server):
    _, rows = get(server, "/api/search?q=contract")
    ids = {r["id"] for r in rows}
    assert "src/contracts/c.py" in ids and any("Contract" in i for i in ids)
    assert all(r["label"] in ("Unit", "Symbol") for r in rows)


def test_node_and_neighbors(server):
    _, node = get(server, "/api/node?id=src/contracts/c.py")
    assert node["label"] == "Unit" and node["path"] == "src/contracts/c.py"
    _, hood = get(server, "/api/neighbors?id=src/contracts/c.py&labels=IMPORTS&direction=out")
    assert {e["dst"] for e in hood["edges"]} == {"src/gateway/client.py"}
    assert {n["id"] for n in hood["nodes"]} >= {"src/contracts/c.py", "src/gateway/client.py"}
    assert status(server, "/api/node?id=nope") == 404


def test_subtree_reaches_the_syntax_tier(server):
    _, hood = get(server, "/api/subtree?id=src/contracts/c.py&depth=1")
    assert hood["nodes"] and all(n["label"] == "Ast" for n in hood["nodes"])
    assert all(e["label"] == "CHILD" for e in hood["edges"])


def test_source_stays_inside_the_root(server):
    _, src = get(server, "/api/source?path=src/contracts/c.py&from=1&to=2")
    assert src["lines"][0].startswith("from src.gateway") and src["from"] == 1
    assert status(server, "/api/source?path=../../conftest.py") == 403
    assert status(server, "/api/source?path=missing.py") == 404


def test_query_is_refused_unless_allowed(server):
    assert status(server, "/api/query", data=b'{"cypher": "MATCH (n) RETURN count(n) AS c"}') == 403


def test_extra_routes_are_mounted(server):
    assert get(server, "/api/hello?x=1")[1] == {"hi": "1"}
    assert status(server, "/api/nothing") == 404
