# srcgraph

Source code in, a queryable two-tier knowledge graph out. The engine under adrgraph.

## Context for agents

- Consumer: adrgraph at `~/Documents/adr-as-code` (github.com/crunchdomo/adrgraph). It depends on this
  package (`srcgraph>=0.2,<0.3`) through the schema-extension API in `srcgraph/graph.py` and must never be
  imported here (ADR-0001, enforced by `adrgraph check` on this repo).
- Design history lives in adrgraph's repo under `docs/superpowers/specs/` and `docs/superpowers/plans/`:
  milestone 1 (enforcer), milestone 2 (two-tier graph), milestone 3 (this split + provenance). The next
  planned piece for this repo is the explorer (`srcgraph serve`), designed in
  `2026-11-06-srcgraph-split-and-provenance-design.md` under "Decided: the explorer".
- Dev setup: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'`; `pytest`. `~/Documents` is
  iCloud-synced, which sometimes hides editable `.pth` files; if `import srcgraph` fails outside pytest,
  `chflags nohidden .venv/lib/python3.*/site-packages/*.pth`.
- After changing this package, adrgraph's venv needs `pip install ~/Documents/srcgraph` (non-editable)
  and then `rm -rf ~/Documents/srcgraph/build`.
- Self-check: `adrgraph check --exclude tests/fixtures` (adrgraph installed as a dev tool).
- Acceptance against real repos (colon-separated): `SRCGRAPH_ACCEPT_REPO=~/Documents/f1-race-replay:~/Documents/adr-as-code:~/Documents/kg-wiki:~/Documents/jobapps pytest tests/test_acceptance.py`.
  Skipped when the variable is unset. The unresolved-call ceiling in that file is lowered as the call-graph tickets land.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for `crunchdomo/srcgraph` (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
