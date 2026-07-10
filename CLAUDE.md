## Workflow

Planning uses a portable convention — `architecture/` (repo root) is the living
**truth home** and promotion target; `planning/changes/` holds the per-change
files. Start at the
[Quick path](planning/README.md#quick-path-start-here) in `planning/README.md`
(the authoritative spec) to pick a lane — **Full** (design template),
**Lightweight** (change template), or **Tiny** (just a commit) — and ship.
`just check-planning` validates changes; `just index` prints the change +
decision listing; `planning/_templates/` are copy-and-fill starting points.

**When a change alters a capability's behavior, update the matching
`architecture/<capability>.md` in the same PR** — that promotion is what keeps
`architecture/` true.
