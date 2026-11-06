# srcgraph

Source code in, a queryable two-tier knowledge graph out.

srcgraph parses a repository with tree-sitter and loads two tiers into an embedded [LadybugDB](https://github.com/LadybugDB/ladybug) graph: a **semantic tier** of units, symbols, imports, calls, inheritance and decorators that is language-neutral, and a **syntax tier** holding every named node of every file's syntax tree. You query it in Cypher. No server; the database is a file rebuilt on every run.

It is the engine under [adrgraph](https://github.com/crunchdomo/adrgraph), which enforces architecture decision records against it, and it is useful on its own for any question of the form "who touches X", "what does Y reach", or "show me every Z in this codebase".

## Install

```bash
pipx install srcgraph        # or pip install srcgraph in a virtualenv
# until it is on PyPI:
pipx install git+https://github.com/crunchdomo/srcgraph@v0.1.0
```

Python 3.11 or later. Python and Terraform (HCL) are supported.

## Use

```bash
srcgraph build                                   # scans . into .srcgraph/graph.lbug
srcgraph query "MATCH (u:Unit)-[:IMPORTS]->(v:Unit {kind:'external'}) RETURN v.name, count(*) ORDER BY count(*) DESC LIMIT 10"
srcgraph query "MATCH (c)-[:INHERITS]->(b) WHERE b.name = 'BaseCommand' RETURN c.qualified"
srcgraph query "MATCH (a:Ast {norm:'Decorator'}) RETURN a.text, count(*) ORDER BY count(*) DESC"
```

`--src` repeats and sets the import roots: with a `src/` layout pass `--src src`. `--exclude` adds names or globs to the defaults (`.git`, `.venv`, `node_modules`, `.terraform`, ...).

## The graph

**Semantic tier.** `Unit` (file, package, module, or an `external` import such as `logging`) and `Symbol` (function, method, class, variable, Terraform resource, ...). Both carry `doc`, the first paragraph of their docstring, so "what is this for" is a query, not a file read. Edges, each carrying the source `line`: `CONTAINS`, `IMPORTS`, `CALLS`, `REFERENCES`, `INHERITS`, `DECORATES`. Unresolved names become `Symbol` nodes of kind `unresolved` so nothing is silently dropped.

**Syntax tier.** Every named tree-sitter node as an `Ast` node with `kind` (the grammar's name, e.g. `class_definition`), `norm` (a shared name where one applies), `line`, `start_byte`, `end_byte`, `field` (its slot in the parent), and `text` for leaves and short high-value nodes. Edges `CHILD` (with `field`) and `NEXT`. Each `Unit` and `Symbol` has a `DERIVED_FROM` edge to the syntax node it came from.

The normalised vocabulary is fixed in `srcgraph/model.py`: `ClassDef`, `FunctionDef`, `Call`, `Assign`, `Import`, `Decorator`, `Attribute`, `Name`, `String`, `Return`, `If`, `Loop`, `Try`, `Lambda`, `Parameter`, `Argument`, `Block`, `Comment`, `Type`, `Literal`. Extractors map their grammar onto it and never invent names.

## Extending the schema

A consumer adds its own tables at construction and loads them generically:

```python
from srcgraph.graph import Graph
from srcgraph.scan import scan
from srcgraph.model import Edge

g = Graph(".srcgraph/graph.lbug",
          node_tables={"Decision": "id STRING, title STRING, PRIMARY KEY(id)"},
          rel_tables={"GOVERNS": ([("Decision", "Unit")], [])})
g.load_code(scan(["."]))
g.load_nodes("Decision", [{"id": "ADR-1", "title": "Only the gateway talks HTTP"}])
g.load_edges([Edge("GOVERNS", "ADR-1", "src/gateway/client.py")])
```

`compile_selector` turns a small selector map (`path`, `kind`, `name`, `lang` globs) into a parameterised Cypher fragment, and `Source` reads file lines for excerpts.

## Performance

A 17k-line repo builds its full two-tier graph (110k syntax nodes) in about 3 seconds. Django at 531k lines (3.0M syntax nodes, 4.5M edges) builds in about 25 seconds into a 630 MB file; 2-hop queries answer in tens of milliseconds.

## Adding a language

One module in `srcgraph/extractors/` that turns a tree-sitter tree into `Unit`, `Symbol`, `Edge`, `RawImport` and `RawRef` records, a grammar-kind to normalised-kind mapping passed to `emit_syntax`, and a registration in `srcgraph/extractors/__init__.py`. Nothing else changes.

## Licence

MIT.
