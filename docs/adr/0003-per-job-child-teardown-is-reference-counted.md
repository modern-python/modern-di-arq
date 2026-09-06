# Per-job child teardown is reference-counted, not owned

**Decision:** `inject`'s wrapper tracks an open depth on the job's `ctx` and closes the per-job
child on the 1→0 transition. It does not decide, on entry, whether it is the call that owns the
child.

Ownership is the smaller and more obvious mechanism, and it was the first one shipped here: a
boolean taken on entry (then, `owns = child.closed`), closing in `finally` only `if owns`. It reads
correctly and it handles the case that motivated moving teardown out of `on_job_end` — an outer
`@inject` task awaiting an inner `@inject` function over the same `ctx`, where the inner call must
not close a child the outer one is still using.

It is wrong as soon as two `@inject` calls are concurrent rather than nested, and the fault is the
entry-time claim itself, not the state it happened to read. A job coroutine that is not itself
`@inject` can `asyncio.gather` two decorated helpers over the same `ctx`. Both enter before either
exits, so exactly one of them takes the claim and the other defers to it — and the claimant is the
one that entered first, which says nothing about which one exits last. When it is the faster
sibling, its `finally` runs the `Scope.REQUEST` finalizers while the slower sibling is still
awaiting with a resolved handle to what was just finalized. Nothing raises. The symptom is a
resource used after teardown, in the sibling that happened to be slower, which is why an
adversarial review rather than the suite found it.

A count has no such entry-time question to get wrong: the child is closed by whichever call is last
to exit, whether the calls nested or overlapped, and whether or not it is the one that opened it.
Both transitions run with no `await` between the read and the write, so the event loop cannot
interleave another call into the middle of either. The invariant is
`test_concurrent_inject_fanout_shares_one_child` in `tests/test_jobs.py`, which is red under the
boolean version and green under the count.

**Revisit trigger:** the child's lifetime stops being derived from `@inject` spans — arq gaining a
hook that is guaranteed to run after the task on every path, or modern-di growing a per-scope
context manager the wrapper could enter instead. Either removes the bookkeeping rather than
simplifying it, and the count goes with it.
