# srcgraph

Source code in, a queryable two-tier knowledge graph out.

srcgraph parses a repository with tree-sitter and loads two tiers into an embedded [LadybugDB](https://github.com/LadybugDB/ladybug) graph: a **semantic tier** of units, symbols, imports, calls, inheritance and decorators that is language-neutral, and a **syntax tier** holding every named node of every file's syntax tree. You query it in Cypher. No server; the database is a file rebuilt on every run.

It is the engine under [adrgraph](https://github.com/crunchdomo/adrgraph), which enforces architecture decision records against it, and it is useful on its own for any question of the form "who touches X", "what does Y reach", or "show me every Z in this codebase".

## Install

```bash
pipx install srcgraph        # or pip install srcgraph in a virtualenv
```

Releases are published to PyPI by CI when a `v*` tag is pushed.

Python 3.11 or later. Python and Terraform (HCL) are supported.

## Use

```bash
srcgraph build                                   # scans . into .srcgraph/graph.lbug
srcgraph query "MATCH (u:Unit)-[:IMPORTS]->(v:Unit {kind:'external'}) RETURN v.name, count(*) ORDER BY count(*) DESC LIMIT 10"
srcgraph serve                        # build, then open the explorer at http://127.0.0.1:8765 (--no-build, --port, --allow-query)
srcgraph query "MATCH (c)-[:INHERITS]->(b) WHERE b.name = 'BaseCommand' RETURN c.qualified"
srcgraph query "MATCH (a:Ast {norm:'Decorator'}) RETURN a.text, count(*) ORDER BY count(*) DESC"
srcgraph importers PySide6                       # every unit importing PySide6 or a submodule, with lines
srcgraph callers get_tyre_compound_int           # who calls it, by name or qualified name, with lines
srcgraph scope 'src/gui/**'                      # one compartment: its files, internal wiring, outbound and inbound wires (--no-tests)
srcgraph domains api='src/api/**' billing='src/billing/**' orders='src/orders/**'   # who leans on whom, and every crossing edge
srcgraph dead                                    # symbols nothing reaches and that are not entry points: deletion candidates
srcgraph hubs                                    # most distinct neighbours in and out: what you must not break, and what does too much
srcgraph untested                                # symbols no test reaches through calls
srcgraph visibility                              # per file, calls the graph resolved vs calls it cannot see: read the answers above against this
srcgraph trace 'src.lib.time.parse_time_string("1:23.456")'   # run it and print the calls it makes through the graph
srcgraph trace tests/lib/test_time.py::test_format_time_valid_values   # same, for one pytest test
```

`scope` is the airplane view: pick a compartment by path and see what stays in the room, what the room controls, and what controls it. The header lists every file in the compartment with its doc line; a blank doc is an undocumented file. `--no-tests` hides wires that touch a test unit, leaving the real consumers.

`domains` is the same view for a repo with several bounded contexts in it: name each domain with a path glob and the wires roll up to a from/to/edges matrix, followed by every crossing edge with its source, label, target and line. Wires inside a domain and paths that match no domain are left out, so the output is exactly the coupling between the parts you named. On the test fixture, `handlers=src/handlers/** contracts=src/contracts/** db=src/db/** gateway=src/gateway/**` gives three pairs (handlers→contracts 3, contracts→gateway 2, handlers→db 2) and seven crossings. Nothing says "a domain may not do that": `adrgraph` is where the rules live, this is the evidence.

The presets answer the common questions without Cypher; `--format json` works on all of them and they exit 1 when nothing matches. `dead`, `hubs` and `untested` are the structure questions: where to look first when tidying a codebase. They name candidates, not verdicts, since `getattr`, registries and plugins are invisible to a static graph. A name imported from a package (`from flask import Flask`) follows the package's `__init__` re-exports to the symbol it names, up to three hops, so a library's public API is not reported dead because its tests reach it through the facade; an `__all__` built at runtime stays invisible. `dead` keeps a method only when nothing anywhere calls `.name` on an instance, because the graph has no types to say which class an instance is. It also skips decorated symbols (routes, tasks, signal handlers are registered, not called) and every method on a class that inherits from something outside the repo, since those are usually hooks the framework calls (`paintEvent`, `handle_data`). `hubs` counts distinct neighbours, so a script that calls one helper forty times is not a hub. `untested` refuses to run when the graph has no test units. `visibility` is the caveat on all of them: per file, how many calls landed on a scanned symbol and how many went to something the graph cannot see. The biggest blind spot is a method called on anything other than `self` or a module (`request.getfixturevalue(...)`): without types there is no target, so no edge is recorded at all, and `callers` of such a method says nothing calls it. Read an empty answer next to the file's `visibility` row before believing it.

`trace` is one flight: the static graph is the state machine (functions are states, CALLS edges the transitions) and a trace is one run of it. Each CALLS edge carries a `guard`, the nearest enclosing `if`, `for`, `while` or `try` as `kind@line`, so the static side also says roughly when a transition fires; `callers` and `scope` print it as the last column. The input is a Python expression or a pytest node id, which runs that one test with the test function at the root. It runs in the `python` on your PATH, so activate the target repo's venv first and run it where you ran `build`. Only functions in the graph appear; library and stdlib frames are dropped. Each call prints as `qualified(arg=value)`, its return as `= value  (ms)`, and an exception as `! Type: message` at every frame it passes through. Values are cut at 60 characters, containers show as `list[1200]`, anything with a `shape` as `DataFrame(1200×12)`, and a run of three or more calls to the same function collapses to the first, `× N calls (ms)`, and the last. That makes a trace a profiler over your own code only. Every trace is saved under `.srcgraph/traces/`; the explorer lists them and draws the chosen run as a route on the graph, with the rest dimmed.

`--src` repeats and sets the import roots: with a `src/` layout pass `--src src`. Pointing a root at a package directory itself (one with `__init__.py`) names its modules without the package prefix, so `from pkg.x import y` inside it goes unresolved; the build warns and tells you to scan the parent. It also warns when it finds no source files at all. `--exclude` adds names or globs to the defaults: every hidden directory, `venv`, `node_modules`, `__pycache__`. A pattern without `/` matches one path component at any depth; with `/` it matches from the root. Inside a git repository the file list comes from `git ls-files`, so anything `.gitignore` hides (build output, `cdk.out/`, generated code) is never scanned; untracked files that are not ignored still are.

## As a library

The same answers from Python, for notebooks, scripts and tools built on top (adrgraph is one):

```python
import srcgraph

g = srcgraph.load(".")              # builds .srcgraph/graph.lbug when missing; rebuild=True forces it
g.dead()                            # the rows the CLI prints, as a list of dicts
g.hubs(); g.untested()
g.callers("score_one"); g.importers("requests")
g.scope("app/**")                   # {"doc": [...], "internal": [...], "outbound": [...], "inbound": [...]}
g.trace("app.predictor.score_one({...})")   # call/return/exc events; runs in the repo's root
g.query("MATCH (a:Unit)-[:IMPORTS]->(b:Unit {kind: 'external'}) RETURN b.name, count(*) ORDER BY count(*) DESC")
```

Rows are plain dicts, so `pandas.DataFrame(g.hubs())` works without srcgraph knowing about pandas. `load` accepts
`db`, `src` (source roots relative to the root) and `excludes`; `Repo` is a context manager. The CLI presets call the
same object, so the two cannot disagree.

## The graph

**Semantic tier.** `Unit` (file, package, module, or an `external` import such as `logging`) and `Symbol` (function, method, class, variable, Terraform resource, ...). Both carry `doc`, the first paragraph of their docstring, so "what is this for" is a query, not a file read. A `Unit` has `test` (under a `tests` directory or named `test_*.py`); a `Symbol` has `entry` (reached from a `__main__` guard, a console script in `pyproject.toml`, or a test). Edges, each carrying the source `line`: `CONTAINS`, `IMPORTS`, `CALLS`, `REFERENCES`, `INHERITS`, `DECORATES`. `REFERENCES` covers a dotted attribute read and a bare name used as a value or annotation (`pool.map(worker, ...)`, `def f(x: Thing)`), the latter only when it resolves to a scanned symbol outside the reader's own globals. Calls to Python builtins land on one shared `Symbol` of kind `builtin` per name (`builtins.print`), unless the module defines or imports its own. Other unresolved names become `Symbol` nodes of kind `unresolved` so nothing is silently dropped.

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

## Explorer

`srcgraph serve` builds the graph and serves a single-page explorer on localhost: search units and symbols, click a result to put it on the canvas with its neighbours, double-click a node to expand it, filter by edge label, and read the node's properties and source excerpt in the detail panel. "Syntax tree" drops the node's syntax subtree onto the canvas. The page is one HTML file with `d3` from a pinned CDN URL; the server is the standard library's `http.server`.

JSON routes under `/api/` (`search?q=`, `node?id=`, `neighbors?id=&labels=&direction=`, `subtree?id=&depth=`, `source?path=&from=&to=`) back the page and are usable on their own. `POST /api/query` runs raw Cypher and is off unless the server was started with `--allow-query`. A consumer can mount more routes; `adrgraph serve` adds a findings overlay this way.
