---
id: ADR-0003
title: The core owns the normalised syntax vocabulary
status: accepted
owner: maintainers
date: 2026-11-06
review_date: 2027-11-06

scope:
  - { path: "srcgraph/model.py" }
---

## Context

The syntax tier stores every named node of every file with two names: the grammar's own
node type (`class_definition`, `block`) and a normalised kind (`ClassDef`) where one applies.
A normalised rule only means the same thing in two languages if both extractors map onto the
same list. If each extractor could invent names, the shared vocabulary would drift apart the
first time two people added a language independently.

## Decision

`NORM_KINDS` and `TEXT_NORMS` live in `srcgraph/model.py` and are the only normalised names
that exist. An extractor owns a mapping from its grammar's node types onto that list and leaves
anything unmapped as an empty normalised kind. Adding a name to the list is a core change with
a reason, never a side effect of adding a language.

## Consequences

A rule written against `{norm: 'Decorator'}` is portable to any language whose mapping covers
decorators, and `adrgraph audit` can report how portable each rule is. The cost is that a
grammar construct with no good normalised equivalent stays unnormalised and is only reachable
by its grammar kind. Enforcement today is this ADR's `scope` on `model.py`; once a `WRITES`
edge exists the rule becomes `never from {path: "srcgraph/extractors/**"} to
{name: "srcgraph.model.NORM_KINDS"} via WRITES`.
