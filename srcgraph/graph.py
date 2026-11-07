"""LadybugDB-backed property graph: schema, bulk load via COPY FROM, query. The only module that imports ladybug.

The graph owns the code tables; a consumer may add its own node and rel tables at construction
(`node_tables`, `rel_tables`) and load them with the generic `load_nodes` / `load_edges`."""
import csv
import os
import tempfile
from collections import defaultdict

import ladybug

from srcgraph.model import Edge, Extraction

NODE_TABLES = {
    "Unit": "id STRING, path STRING, name STRING, lang STRING, kind STRING, line INT64, doc STRING, test BOOLEAN, PRIMARY KEY(id)",
    "Symbol": "id STRING, name STRING, qualified STRING, kind STRING, lang STRING, path STRING, "
              "line INT64, end_line INT64, ast_kind STRING, doc STRING, entry BOOLEAN, PRIMARY KEY(id)",
    "Ast": "id STRING, path STRING, kind STRING, norm STRING, lang STRING, line INT64, end_line INT64, "
           "start_byte INT64, end_byte INT64, text STRING, field STRING, PRIMARY KEY(id)",
}
_REF_PAIRS = [("Symbol", "Symbol"), ("Symbol", "Unit"), ("Unit", "Symbol"), ("Unit", "Unit")]
# label -> (from/to pairs, extra property columns)
REL_TABLES = {
    "CONTAINS": ([("Unit", "Unit"), ("Unit", "Symbol"), ("Symbol", "Symbol")], ["line"]),
    "IMPORTS": ([("Unit", "Unit")], ["line"]),
    "CALLS": ([("Symbol", "Symbol"), ("Unit", "Symbol")], ["line", "guard"]),
    "REFERENCES": (_REF_PAIRS, ["line"]),
    "INHERITS": ([("Symbol", "Symbol")], ["line"]),
    "DECORATES": ([("Symbol", "Symbol")], ["line"]),
    "DERIVED_FROM": ([("Unit", "Ast"), ("Symbol", "Ast")], []),
    "CHILD": ([("Ast", "Ast")], ["field"]),
    "NEXT": ([("Ast", "Ast")], []),
}
_PROP_TYPES = {"line": "INT64", "field": "STRING", "guard": "STRING"}


def escape(s: str) -> str:
    """CSV string cell: LadybugDB's parallel reader rejects quoted newlines."""
    return s.replace("\r", "\\r").replace("\n", "\\n")


def _columns(ddl: str) -> list[str]:
    return [c.split()[0] for c in ddl.split(", ") if not c.startswith("PRIMARY")]


class Graph:
    def _schema(self, node_tables, rel_tables) -> None:
        for name in (node_tables or {}):
            if name in NODE_TABLES:
                raise ValueError(f"extension node table '{name}' shadows a srcgraph table")
        for name, (_, props) in (rel_tables or {}).items():
            if name in REL_TABLES:
                raise ValueError(f"extension rel table '{name}' shadows a srcgraph table")
            for prop in props:
                if prop not in _PROP_TYPES:
                    raise ValueError(f"rel table '{name}': unknown property '{prop}' (allowed: {', '.join(sorted(_PROP_TYPES))})")
        self.node_tables = {**NODE_TABLES, **(node_tables or {})}
        self.rel_tables = {**REL_TABLES, **(rel_tables or {})}
        self._cols = {t: _columns(ddl) for t, ddl in self.node_tables.items()}
        self._table_of: dict[str, str] = {}

    def __init__(self, path: str, node_tables: dict[str, str] | None = None,
                 rel_tables: dict[str, tuple[list[tuple[str, str]], list[str]]] | None = None):
        self._schema(node_tables, rel_tables)
        for p in (path, path + ".wal"):
            if os.path.exists(p):
                os.remove(p)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = ladybug.Database(path)
        self._conn = ladybug.Connection(self._db)
        for table, ddl in self.node_tables.items():
            self._conn.execute(f"CREATE NODE TABLE {table}({ddl})")
        for label, (pairs, props) in self.rel_tables.items():
            spec = ", ".join(f"FROM {a} TO {b}" for a, b in pairs)
            cols = "".join(f", {p} {_PROP_TYPES[p]}" for p in props)
            self._conn.execute(f"CREATE REL TABLE {label}({spec}{cols})")

    @classmethod
    def open(cls, path: str, node_tables: dict[str, str] | None = None,
             rel_tables: dict[str, tuple[list[tuple[str, str]], list[str]]] | None = None,
             read_only: bool = False) -> "Graph":
        """Open an existing database without rebuilding the schema. Pass the same extensions it was
        built with to load into them. `read_only` is for long-lived readers such as `serve`."""
        if not os.path.exists(path):
            raise RuntimeError(f"{path} does not exist; build the graph first")
        g = cls.__new__(cls)
        g._schema(node_tables, rel_tables)
        g._db = ladybug.Database(path, read_only=read_only)
        g._conn = ladybug.Connection(g._db)
        return g

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        result = self._conn.execute(cypher, params or {})
        cols = result.get_column_names()
        rows = []
        while result.has_next():
            rows.append(dict(zip(cols, result.get_next())))
        result.close()
        return rows

    def count(self, label: str) -> int:
        return self.query(f"MATCH (n:{label}) RETURN count(*) AS c")[0]["c"]

    def _copy(self, table: str, filename: str, rows: list[list], options: str = "") -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, filename)
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh, quoting=csv.QUOTE_NONNUMERIC)  # quoted "" loads as '', an empty cell as NULL
                for row in rows:
                    w.writerow([escape(v) if isinstance(v, str) else v for v in row])
            self._conn.execute(f"COPY {table} FROM '{path}'{options}")

    def load_nodes(self, table: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = self._cols[table]
        for row in rows:
            self._table_of[row["id"]] = table
        self._copy(table, f"{table}.csv", [[row[c] for c in cols] for row in rows])

    def load_edges(self, edges: list[Edge], props: dict[tuple[str, str, str], dict] | None = None) -> None:
        """Edges whose endpoints are not loaded nodes are dropped. `props` supplies property values other
        than `line` (which comes from the Edge), keyed by (label, src, dst)."""
        props = props or {}
        unknown = {i for e in edges for i in (e.src, e.dst) if i not in self._table_of}
        if unknown:
            for row in self.query("MATCH (n) WHERE n.id IN $ids RETURN n.id AS id, label(n) AS t", {"ids": sorted(unknown)}):
                self._table_of[row["id"]] = row["t"]
        grouped: dict[tuple[str, str, str], list[list]] = defaultdict(list)
        for e in edges:
            src, dst = self._table_of.get(e.src), self._table_of.get(e.dst)
            pairs, labels = self.rel_tables[e.label]
            if src and dst and (src, dst) in pairs:
                extra = props.get((e.label, e.src, e.dst), {})
                own = {"line": e.line, "guard": e.guard}
                grouped[(e.label, src, dst)].append([e.src, e.dst, *[own[p] if p in own else extra.get(p, "") for p in labels]])
        for (label, src, dst), rows in grouped.items():
            self._copy(label, f"{label}_{src}_{dst}.csv", rows, f" (from='{src}', to='{dst}')")

    def load_code(self, result: Extraction) -> None:
        self.load_nodes("Unit", [{**vars(u), "line": 1} for u in result.units])
        self.load_nodes("Symbol", [vars(s) for s in result.symbols])
        self.load_nodes("Ast", [vars(a) for a in result.ast])
        field = {a.id: a.field for a in result.ast}
        props = {("CHILD", e.src, e.dst): {"field": field.get(e.dst, "")} for e in result.ast_edges if e.label == "CHILD"}
        self.load_edges(list(result.edges) + list(result.ast_edges), props)

    def close(self) -> None:
        self._conn.close()
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
