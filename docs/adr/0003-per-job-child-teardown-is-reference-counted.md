# Per-job child teardown is reference-counted, not owned

`inject`'s wrapper keeps an open depth on the job's `ctx` and closes the `Scope.REQUEST` child on
the 1 to 0 transition. The boolean claim it replaced, `owns = child.closed` taken on entry and
honoured in `finally`, is smaller and handles the nested case that moved teardown out of
`on_job_end`, but it is wrong as soon as two `@inject` calls overlap rather than nest: a job
coroutine that is not itself decorated can `asyncio.gather` two decorated helpers over one `ctx`,
the claim goes to whichever entered first, and when that one also exits first it runs the
REQUEST-scoped finalizers while its sibling still holds a resolved handle, with nothing raising. A
count has no entry-time question to get wrong, and each transition reads and writes with no `await`
between, so the loop cannot interleave; `on_job_end` then closes the child only as a safety net for
a job that ran no wrapper. `tests/test_jobs.py::test_concurrent_inject_fanout_shares_one_child` is
red under the boolean and green under the count.
