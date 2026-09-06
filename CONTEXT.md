# modern-di-arq

An [arq](https://arq-docs.helpmanual.io) adapter over
[`modern-di`](https://github.com/modern-python/modern-di): it wires a container into an arq
worker's lifecycle hooks and resolves a task's `FromDI`-marked parameters from a container scoped
to the job that is running it.

## Language

A term is listed only when there is a synonym to reject, or a meaning subtle enough that code and
docs must agree on it. General programming vocabulary does not belong here, however heavily this
package uses it.

The domain terms are `modern-di`'s — `Container`, `Provider`, `Group`, `Scope`, `Resolution`,
`Override`. That project's `CONTEXT.md` is the authority for all of them; nothing here redefines
one. arq owns the rest — `worker`, `ctx`, and its four `on_*` lifecycle hooks — used here in arq's
own sense. The three below are what this integration has to keep straight itself.

**Task**:
A coroutine listed in `WorkerSettings.functions`, optionally decorated with `@inject`. It is
declared once, at import time, and outlives every run of it.

**Job**:
One enqueued execution of a task: the span arq brackets with `on_job_start`/`on_job_end`, and the
lifetime of one `ctx` dict.
_Avoid_: task. Celery — the sibling integration this one is shaped after — calls the enqueued unit
a task, so the two get swapped without anyone noticing. Here a task is the callable and a job is
one run of it, and everything scoped per job turns on the difference.

**Per-job child**:
The `Scope.REQUEST` container built off the root for a single job. Its lifetime is not the hook
pair that brackets it: it is closed exactly once, by whichever `@inject` wrapper is *last* to exit,
with `on_job_end` closing it only as a safety net for a job that ran no wrapper at all.
