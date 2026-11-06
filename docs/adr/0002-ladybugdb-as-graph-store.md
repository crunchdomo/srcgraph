---
id: ADR-0002
title: LadybugDB is the graph store and the database file is disposable
status: accepted
owner: maintainers
date: 2026-11-05
review_date: 2027-05-05
rules:
  - id: ladybug-only-in-graph
    description: Only graph.py may import ladybug, so the store can be swapped behind one module.
    only:
      from: { path: "srcgraph/graph.py" }
      to: { kind: unit, name: "ladybug" }
      via: IMPORTS
---

## Context

The tool needs a real property graph with Cypher so rules and ad hoc questions are expressive,
but a build that runs in CI cannot require a database server. LadybugDB (the renamed Kùzu) is
embedded, in-process, and Cypher-native. It is a 0.x project with a young release cadence.

## Options considered

- Neo4j or Memgraph: real graphs, but a server in CI.
- networkx in memory: no server, no Cypher, no exploration story.
- LadybugDB: embedded, Cypher, columnar, pip-installable.

## Decision

LadybugDB, confined to `srcgraph/graph.py`. The database file under `.srcgraph/` (or wherever a
consumer points `--db`) is a build artifact: rebuilt from source on every run, git-ignored, never
the only copy of anything. Loading uses `COPY FROM` temporary CSV files; per-edge inserts were
measured at roughly an hour for a 220k-edge syntax tier.

## Consequences

Queries are Cypher and port to other engines if LadybugDB stalls. Crash-recovery bugs in the
store cannot lose data because nothing is stored only there. Swapping the engine is a one-module
change.
