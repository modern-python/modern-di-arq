# Architecture

The living truth about what `modern-di-arq` does **now** — one file per
capability, updated by hand whenever a change ships. The *why* and *how it got
here* live in [`../planning/changes/`](../planning/changes/), and decisions
deliberately taken (including options rejected) in
[`../planning/decisions/`](../planning/decisions/); this directory is the present.

These files carry **no frontmatter** — they are prose, dated by git.

## Capabilities

- [`dependency-injection.md`](dependency-injection.md) — wiring a `modern-di`
  container into an arq worker: `setup_di` + the `ctx`-based handoff, the
  per-job container seam, and `FromDI`/`inject` resolution.

## Promotion rule

Shipping a change hand-edits the affected capability file(s) here to match the
new reality, in the same PR as the code. The change file stays in place under
[`../planning/changes/`](../planning/changes/) — no folder move.
