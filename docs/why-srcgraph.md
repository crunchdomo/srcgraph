# Why srcgraph exists, and who it is for

srcgraph turns a repository into a graph you can query: modules, symbols, and the wires between them
(imports, calls, inheritance, decorators), plus every node of every syntax tree underneath. This page
is the one answer to "who is this for" and "why not just ask an AI". Tickets link here.

## Who it is for

**A developer writing tools over a codebase.** They want "repo in, queryable graph out" as one import
and never want to touch tree-sitter. srcgraph is a library first; `from srcgraph import scan, Graph`
is the product. The CLI is the smallest tool built that way, kept small so that anyone reading it
sees how to build their own. adrgraph, which enforces architecture decisions against the graph, was
the first such tool.

**Two humans at the terminal.** The maintainer who knows the repo and wants one fact fast:
who calls this, what does this package import, what reaches this file. They type one command, read
ten lines, go back to work. And the newcomer who wants a map before facts: open the explorer, search
a module, read what it is for and what it is wired to. Both stand on the same library.

## The five tools it should make easy

These are the tools the first developer intends to write. Each imposes requirements on the library.

| Tool | What it does | What it needs from the graph |
|---|---|---|
| Blast radius for a pull request | From a diff, walk inbound call and import edges and list the modules, functions and tests the change reaches | Trustworthy call edges; tests identifiable |
| Dead code finder | Symbols with no inbound call, reference or import, minus entry points and tests | Trustworthy call edges; entry points identifiable |
| The manual | For each package: its doc line, what it controls, what controls it, regenerated on every commit | Doc lines; scope query |
| Navigation server for coding agents | Find symbol, who calls it, what it calls, served over MCP or HTTP | Fast open of an existing graph; stable ids; doc lines |
| Test selection | Walk from changed symbols to the tests that reach them and run only those | Trustworthy call edges; tests identifiable |

Four of the five have no AI in them. They are plain programs a human or CI runs.

## Requirements

1. **The call graph is trustworthy.** Blast radius, dead code and test selection walk call edges; a
   graph where most calls land on unresolved names makes all three lie. Builtins resolve to one shared
   node, calls through `self` resolve to the enclosing class, and calls through locals are dropped
   rather than invented.
2. **Opening a built graph is fast and ids are stable across builds.** Servers and bots open a graph
   someone else built. Ids are path plus qualified name and must stay that way.
3. **Every unit and symbol carries its doc line**, the first paragraph of its docstring. "What is this
   for" is answered by the graph, not by opening the file.
4. **A scope query** returns one compartment's internal wiring, outbound wires and inbound wires.
   The manual and the explorer's detail panel are both views of it.
5. **Tests and entry points are identifiable.** Tests by path convention; entry points by `__main__`
   guards, declared console scripts, and anything a test calls.
6. **A human can get the common facts without writing Cypher.** Presets for callers, importers and
   scope. Cypher stays for the question nobody anticipated.
7. **Build time stays in seconds for a mid-size repo.** A 17k-line repo builds in about 6 seconds;
   Django at 531k lines builds in about 25. Incremental builds are not needed until that is in
   someone's way.

## Non-goals

- Runtime behaviour. The graph is the wiring diagram, not the flight recorder: it knows a call site
  exists, not whether, when, or with what it ran.
- Data flow and type inference beyond `self`. `self.engine.start()` resolving through the type of
  `engine` is out of scope for a tree-sitter tool.
- Languages beyond Python, Lua and Terraform until a tool needs one. The extractor registry is the seam.

## Why a graph and not a model

srcgraph works with no AI anywhere near it, and is worth having on those terms. Where an AI is
present, it is one more client. The reasons a model cannot replace the graph:

- **Exhaustive, not sampled.** Ask a model who calls a function and it greps, reads a few files, and
  answers from what it saw. It is usually right and never knows when it is not. The graph returns
  every call edge with its line. For blast radius, dead code and test selection, one missed caller is
  a broken deploy.
- **Same answer twice.** A CI gate, a PR comment, a test selector must be reproducible or nobody
  trusts a red build. There is no model in the loop of `adrgraph check` for this reason. A model can
  act on the result; it cannot be the result.
- **Scale a context window cannot reach.** No context window holds Django. The graph does, and a
  two-hop query answers in tens of milliseconds for a few hundred tokens where a model would spend
  tens of thousands reading files for a worse answer.
- **Free and local.** No API call per question, no code leaving the machine, works offline.

When a model is the client, the graph makes its answers complete, repeatable and cheap, and turns
the orientation phase of every session on an unfamiliar repo into a handful of tool calls. An
instrument that reads mostly noise is worse than no instrument, because the pilot trusts it. That is
why the call-graph requirements come first.
