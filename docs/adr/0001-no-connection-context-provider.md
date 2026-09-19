# No connection `ContextProvider` for arq's `ctx`

Most modern-di integrations bind the framework object a unit of work carries, a Starlette `Request`
or a FastStream message, into a `ContextProvider` so a factory deep in the graph can declare it
instead of having it threaded through by hand, and the symmetry argument says arq's per-job `ctx`
should be bound the same way. Neither half of that argument holds here. arq builds `ctx` per job as
`{**worker.ctx, **job_ctx}`, a `dict[str, Any]` of `job_id`, `job_try`, `enqueue_time`, `score` and
`redis`, so there is no named type a factory could ask for, and it calls every task as
`coroutine(ctx, *args, **kwargs)`, so a task already holds it. A provider would only add a second
path to a value that is never missing from the first. What would reopen this is a factory several
levels below the task needing job metadata, which has not appeared.
