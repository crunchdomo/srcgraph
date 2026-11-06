---
id: ADR-0001
title: srcgraph never imports adrgraph
status: accepted
owner: maintainers
date: 2026-11-06
review_date: 2027-11-06
rules:
  - id: never-import-adrgraph
    description: Nothing in the srcgraph package reaches adrgraph, even transitively.
    never:
      from: { path: "srcgraph/**" }
      to: { name: "adrgraph*" }
      via: IMPORTS
      transitive: true
---

## Context

srcgraph turns source code into a queryable graph. adrgraph is one consumer of that graph: it
adds its own tables (decisions, rules) through the schema-extension API and judges code against
ADRs. The dependency points one way: adrgraph requires srcgraph. If srcgraph ever imported
adrgraph, the two could no longer be released, tested or installed separately.

## Decision

No module under `srcgraph/` imports `adrgraph`, directly or through any chain of imports.
adrgraph is installed in this repository only as a development tool, so that this ADR can be
enforced on the code it is about.

## Consequences

Anything ADR-shaped (decision nodes, rule evaluation, findings) lives in adrgraph. srcgraph
exposes what a consumer needs instead: extension tables on `Graph`, `load_nodes`, `load_edges`,
`compile_selector`, and `Source` for excerpts. A violation of this rule fails `adrgraph check`
in CI.
