# No connection `ContextProvider` for arq's `ctx`

**Decision:** this integration registers no connection provider. A task that needs job metadata
reads its `ctx` argument directly.

Most modern-di integrations bind the framework object a unit of work carries — a Starlette
`Request`, a FastStream message, a gRPC `ServicerContext` — into a `ContextProvider`, so a
dependency deep in the graph can declare it and have it resolved rather than threaded through by
hand. The symmetry argument says arq's per-job `ctx` should be bound the same way.

It should not, for two reasons that do not apply to those frameworks. First, `ctx` is not an
object: arq builds it per job as `{**worker.ctx, **job_ctx}`, a `dict[str, Any]` holding `job_id`,
`job_try`, `enqueue_time`, `score`, `redis`, and whatever the worker seeded — a bag with no type
worth declaring a dependency on, so the thing a `ContextProvider` normally buys (a named type a
factory can ask for) is absent. Second, `ctx` is not out of reach: arq calls a task as
`coroutine(ctx, *args, **kwargs)`, so every task already holds it as its first positional
parameter, unconditionally. Injecting it would add a second path to a value that is never missing
from the first one, and the integration would then own a provider whose whole job is to hand back
an argument the caller already has.

`modern-di-celery` and `modern-di-typer` decline for the same reason. Not registering one is the
pattern for a framework whose per-unit context is a bag rather than an object.

**Revisit trigger:** arq replaces the `ctx` dict with a structured context type, or a real request
arrives to resolve job metadata inside a factory several levels below the task — which is the case
that threading `ctx` by hand actually becomes painful, and the case that has not appeared yet.
