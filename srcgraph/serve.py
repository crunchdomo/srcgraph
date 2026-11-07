"""Local explorer: the stdlib HTTP server over an open Graph and a Source reader. Read-only, localhost only.

Routes are GET, JSON, and take their arguments as query parameters (node ids contain '/' and '::').
A consumer mounts more routes with `routes={path: fn(params) -> jsonable}`; adrgraph adds its findings overlay this way."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlparse

from srcgraph.graph import Graph
from srcgraph.source import Source

LIMIT = 100
SYNTAX_LABELS = {"DERIVED_FROM", "CHILD", "NEXT"}   # reached through /api/subtree, not /api/neighbors


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


def _node(d: dict) -> dict:
    return {**{k: v for k, v in d.items() if not k.startswith("_")}, "label": d["_LABEL"]}


class Explorer:
    def __init__(self, graph: Graph, source: Source, traces: str | None = None):
        self.graph, self.source, self.traces_dir = graph, source, traces
        self.edge_labels = sorted(set(graph.rel_tables) - SYNTAX_LABELS)

    def search(self, p: dict) -> list[dict]:
        q = p.get("q", "").lower()
        if not q:
            return []
        rows = []
        for label, cols in (("Unit", ("name", "path")), ("Symbol", ("name", "qualified", "path"))):
            cond = " OR ".join(f"lower(n.{c}) CONTAINS $q" for c in cols)
            rows += self.graph.query(f"MATCH (n:{label}) WHERE {cond} RETURN n LIMIT 50", {"q": q})
        return [_node(r["n"]) for r in rows[:50]]

    def node(self, p: dict) -> dict:
        rows = self.graph.query("MATCH (n) WHERE n.id = $id RETURN n", {"id": p["id"]})
        if not rows:
            raise NotFound(p["id"])
        return _node(rows[0]["n"])

    def neighbors(self, p: dict) -> dict:
        labels = [l for l in p.get("labels", "").split(",") if l] or self.edge_labels
        for l in labels:
            if l not in self.graph.rel_tables:
                raise KeyError(f"unknown edge label {l}")
        direction = p.get("direction", "both")
        limit = min(int(p.get("limit", LIMIT)), LIMIT)
        rel = ":" + "|".join(labels)
        nodes, edges = {}, []
        patterns = {"out": "(a)-[r%s]->(b)", "in": "(b)-[r%s]->(a)"}
        for d, pat in patterns.items():
            if direction not in (d, "both"):
                continue
            for row in self.graph.query(f"MATCH {pat % rel} WHERE a.id = $id RETURN a, r, b LIMIT $n", {"id": p["id"], "n": limit}):
                a, b = _node(row["a"]), _node(row["b"])
                nodes[a["id"]], nodes[b["id"]] = a, b
                src, dst = (a, b) if d == "out" else (b, a)
                edges.append({"label": row["r"]["_LABEL"], "src": src["id"], "dst": dst["id"], "line": row["r"].get("line")})
        if not nodes:
            self.node(p)   # 404 for an unknown id, an empty neighbourhood otherwise
        return {"nodes": list(nodes.values()), "edges": edges}

    def subtree(self, p: dict) -> dict:
        depth = max(1, min(int(p.get("depth", 2)), 6))
        root = self.graph.query("MATCH (n) WHERE n.id = $id OPTIONAL MATCH (n)-[:DERIVED_FROM]->(a:Ast) "
                                "RETURN CASE WHEN label(n) = 'Ast' THEN n.id ELSE a.id END AS root", {"id": p["id"]})
        if not root or not root[0]["root"]:
            raise NotFound(p["id"])
        rid = root[0]["root"]
        rows = self.graph.query(f"MATCH (r:Ast)-[:CHILD*1..{depth}]->(c:Ast) WHERE r.id = $id RETURN DISTINCT c LIMIT 500", {"id": rid})
        nodes = {rid: _node(self.graph.query("MATCH (a:Ast) WHERE a.id = $id RETURN a", {"id": rid})[0]["a"])}
        nodes.update({r["c"]["id"]: _node(r["c"]) for r in rows})
        edges = self.graph.query("MATCH (p:Ast)-[r:CHILD]->(c:Ast) WHERE p.id IN $ids AND c.id IN $ids "
                                 "RETURN p.id AS src, c.id AS dst, r.field AS field", {"ids": list(nodes)})
        return {"nodes": list(nodes.values()), "edges": [{"label": "CHILD", **e} for e in edges]}

    def source_lines(self, p: dict) -> dict:
        path = p["path"]
        full = os.path.realpath(os.path.join(self.source.root, path))
        if not full.startswith(self.source.root + os.sep):
            raise Forbidden(path)
        lines = self.source.lines(path)
        if lines is None:
            raise NotFound(path)
        start, end = max(1, int(p.get("from", 1))), min(len(lines), int(p.get("to", len(lines))))
        return {"path": path, "from": start, "lines": lines[start - 1:end]}

    def _trace_file(self, name: str) -> dict:
        path = os.path.join(self.traces_dir or "", os.path.basename(name))
        if not self.traces_dir or not os.path.isfile(path):
            raise NotFound(name)
        with open(path) as f:
            return json.load(f)

    def traces(self, p: dict) -> list[dict]:
        """Saved runs, newest first: what `srcgraph trace` wrote under <db dir>/traces/."""
        if not self.traces_dir or not os.path.isdir(self.traces_dir):
            return []
        out = []
        for fn in sorted(os.listdir(self.traces_dir), reverse=True):
            if fn.endswith(".json"):
                d = self._trace_file(fn)
                out.append({"file": fn, "input": d.get("input"), "at": d.get("at"),
                            "calls": sum(e["ev"] == "call" for e in d["events"])})
        return out

    def trace(self, p: dict) -> dict:
        """One saved run as a path on the graph: its nodes, RAN edges (caller -> callee, with counts) and call order."""
        d = self._trace_file(p["file"])
        order, edges, stack = [], {}, []
        for e in d["events"]:
            if e["ev"] != "call":
                continue
            del stack[e["depth"]:]
            if stack:
                edges[(stack[-1], e["id"])] = edges.get((stack[-1], e["id"]), 0) + 1
            stack.append(e["id"])
            order.append(e["id"])
        rows = self.graph.query("MATCH (n:Symbol) WHERE n.id IN $ids RETURN n", {"ids": list(dict.fromkeys(order))})
        return {"input": d.get("input"), "nodes": [_node(r["n"]) for r in rows],
                "edges": [{"label": "RAN", "src": a, "dst": b, "n": n} for (a, b), n in edges.items()], "order": order}

    def routes(self) -> dict:
        return {"/api/search": self.search, "/api/node": self.node, "/api/neighbors": self.neighbors,
                "/api/subtree": self.subtree, "/api/source": self.source_lines,
                "/api/traces": self.traces, "/api/trace": self.trace}


def make_server(graph: Graph, source: Source, port: int = 8765, routes: dict | None = None,
                allow_query: bool = False, host: str = "127.0.0.1", traces: str | None = None) -> ThreadingHTTPServer:
    explorer = Explorer(graph, source, traces)
    table = {**explorer.routes(), **(routes or {})}
    page = files("srcgraph").joinpath("explorer.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, fn, params: dict) -> None:
            try:
                self._send(200, json.dumps(fn(params), default=str).encode())
            except NotFound as exc:
                self._send(404, json.dumps({"error": f"not found: {exc}"}).encode())
            except Forbidden as exc:
                self._send(403, json.dumps({"error": f"forbidden: {exc}"}).encode())
            except (KeyError, ValueError, RuntimeError) as exc:
                self._send(400, json.dumps({"error": str(exc)}).encode())

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, page, "text/html; charset=utf-8")
            fn = table.get(url.path)
            if fn is None:
                return self._send(404, b'{"error": "no such route"}')
            self._json(fn, {k: v[0] for k, v in parse_qs(url.query).items()})

        def do_POST(self):
            if urlparse(self.path).path != "/api/query":
                return self._send(404, b'{"error": "no such route"}')
            if not allow_query:
                return self._send(403, b'{"error": "start the server with --allow-query to run Cypher"}')
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or b"{}"))
            self._json(lambda p: graph.query(p["cypher"]), body)

    return ThreadingHTTPServer((host, port), Handler)
