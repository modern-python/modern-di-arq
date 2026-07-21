# Dependency injection

The capability this package exists for: wiring a `modern-di` `Container` into
an arq worker so task parameters resolve from it, scoped per job. Everything
lives in `modern_di_arq/main.py`; the public surface is `setup_di`,
`fetch_di_container`, `FromDI`, and `inject`. The module imports no arq
symbol — it manipulates arq's `ctx` dict, its hook callables, and a
`worker_settings` class/object/dict structurally, so it works against any arq
version that keeps that shape.

## Container handoff (`ctx`)

arq's `ctx` dict is the framework's state store — the worker builds
`self.ctx`, then merges `{**self.ctx, **job_ctx}` into a fresh dict per job
and passes that same dict through `on_job_start`, the task coroutine, and
`on_job_end`. Three named constants hand the container (and its per-job
open/close bookkeeping) through it, so the writer and every reader stay in
provable agreement instead of relying on bare string literals:

- `_ROOT_CONTAINER_KEY = "modern_di_container"` — the root container, seeded
  into the worker `ctx` by `setup_di` and read back by `fetch_di_container`
  and by `on_job_start` (to build the per-job child).
- `_CHILD_CONTAINER_KEY = "modern_di_request_container"` — the per-job
  `Scope.REQUEST` child, built (unopened) on the per-job `ctx` by
  `on_job_start`, and opened/closed by `inject`'s wrapper(s) around the task
  body/bodies they decorate (see "Per-job scope" below). `on_job_end` only
  closes it as a safety net.
- `_CHILD_DEPTH_KEY = "modern_di_request_container_depth"` — the per-job
  open depth counter `inject`'s wrapper reads and writes, so nested and
  concurrent `@inject` calls sharing one `ctx` share one open child and close
  it exactly once (see "Guaranteed teardown" below).

## `setup_di` and hook composition

`setup_di(worker_settings, container)` is the single entry point. It:

1. Reads any existing `ctx` off `worker_settings` (`_get_setting`), sets
   `ctx[_ROOT_CONTAINER_KEY] = container`, and writes `ctx` back
   (`_set_setting`) — preserving whatever else was already in `ctx`.
2. Wraps all four of arq's lifecycle hooks —
   `on_startup`, `on_shutdown`, `on_job_start`, `on_job_end` — replacing each
   with a closure that performs the modern-di step and then (or first, see
   below) calls through to whatever hook was already set.
3. Returns `container`.

`_get_setting`/`_set_setting` dispatch on `isinstance(worker_settings, dict)`:
item access for a `dict` settings mapping, attribute access
(`getattr`/`setattr`) otherwise — so `setup_di` works against both arq's
dominant idiom (a `WorkerSettings` class) and the `dict` form `Worker(**settings)`
accepts.

**Compose ordering** is deliberate and asymmetric between setup and teardown
hooks, so a user hook always observes a live container:

- `on_startup`: `container.open()` runs, *then* the existing hook (the user
  hook starts with an open root).
- `on_job_start`: the `Scope.REQUEST` child is built (unopened), *then* the
  existing hook. The child is **closed** at this point — a user
  `on_job_start` hook cannot resolve from it; only an `@inject`-decorated
  task can (see "Per-job scope").
- `on_job_end`: the existing hook runs *first*, *then* the safety-net close.
  By this point the owning `@inject` wrapper has normally already closed the
  child, so both the existing hook and the safety net usually see it closed.
- `on_shutdown`: the existing hook runs *first*, *then* `container.close_async()`
  (the user hook still has an open root).

`on_job_end` pops the child with `ctx.pop(_CHILD_CONTAINER_KEY, None)` and
closes it only `if child is not None and not child.closed` — a safety net,
not the primary teardown path (see "Per-job scope").

## Root lifecycle

`open()`/`close_async()` bookend the worker process, not any single job:

- A fresh `Container` is already open on construction, and `Container.open`
  is a no-op when already open. `on_startup` calling `container.open()`
  unconditionally means a second `on_startup` firing on the same container —
  a worker restart, a test re-entry — reopens it instead of raising
  `ContainerClosedWarning`.
- `on_shutdown` calls `container.close_async()`, running every APP-scoped
  finalizer that was registered by a resolution during the worker's
  lifetime.

## Per-job scope

`on_job_start` builds one `Scope.REQUEST` child per job —
`root.build_child_container(scope=Scope.REQUEST)` — and stashes it,
**unopened**, under `_CHILD_CONTAINER_KEY` on that job's `ctx`. It is not
opened here: a `Scope.REQUEST` child is only ever open while an `@inject`
wrapper's call is on the stack (see below), so building it unopened means
nothing has been resolved yet if a user `on_job_start` hook then raises —
there is nothing to leak.

### Guaranteed teardown: the wrapper reference-counts open/close

`inject`'s wrapper is the task body and runs inside arq's own caught
`try` (`run_job` catches a task's exception itself), so a `finally` there is
guaranteed to run — including when arq's `on_job_end` is later skipped, e.g.
it fails to serialize an unpicklable job result/exception after the task
already returned. The wrapper tracks a per-job **depth counter** under
`_CHILD_DEPTH_KEY` on `ctx`, opening on the 0→1 transition and closing on the
1→0 transition:

```python
depth = ctx.get(_CHILD_DEPTH_KEY, 0)
if depth == 0:
    child.open()
ctx[_CHILD_DEPTH_KEY] = depth + 1
try:
    ...  # resolve markers, call func
finally:
    ctx[_CHILD_DEPTH_KEY] -= 1
    if ctx[_CHILD_DEPTH_KEY] == 0:
        await child.close_async()
```

The check/open/increment (and the decrement/close) run with no `await` in
between, so a single `@inject` call's entry and exit are each atomic from
asyncio's point of view — the event loop cannot interleave another task's
entry into the middle of one. This is what makes both **nested and
concurrent `@inject`** safe:

- **Nested**: an outer `@inject` task that awaits an inner
  `@inject`-decorated function passing the same `ctx` finds the child already
  open on the inner call (`depth=1`), increments to `depth=2`, and its own
  `finally` only decrements back to `1` — it does not close. Only the outer
  call's `finally`, decrementing `1→0`, closes the child.
- **Concurrent fan-out**: a job coroutine that is *not* itself `@inject` can
  `asyncio.gather` two or more `@inject`-decorated helpers over the same
  `ctx`. Whichever sibling's task step runs first opens the child (`0→1`);
  every other sibling that enters before the child is closed again just
  increments the depth and reuses it. Each sibling's own `finally`
  decrements; the child is closed exactly once, when the *last* sibling to
  exit brings the depth back to `0` — regardless of which sibling happened to
  open it or which finishes first. Earlier boolean ownership (`owns =
  child.closed`) got this wrong under fan-out: whichever sibling finished
  first believed it alone owned the child and closed it in its `finally`,
  even while a slower sibling was still mid-body — a use-after-finalize on
  the REQUEST-scoped resource the slower sibling was still holding.

A task with no `FromDI` parameter, or one that isn't decorated with
`@inject`, never opens the child at all — it stays closed for that job's
entire span, and `on_job_end`'s safety net finds nothing to close.

### `on_job_end` is a safety net, not the primary teardown

`on_job_end` pops the child and the depth counter
(`ctx.pop(_CHILD_DEPTH_KEY, None)`, for cleanliness — the depth is job-scoped
and the `ctx` dict does not outlive the job) and closes the child **only if
still open** (`if child is not None and not child.closed`). In the ordinary
case this is a no-op: the last `@inject` wrapper to exit already closed the
child before the task returned, or no `@inject` task ever opened it. It only
does real work if something left the child open across the task boundary
(e.g. code that opens and resolves from the child directly instead of via
`@inject`). `Container.open()`/`close_async()` are both idempotent, so this
composes safely with the wrapper regardless of ordering.

### Contract change from `on_job_start` unconditionally opening the child

Earlier versions opened the child in `on_job_start` and closed it
unconditionally in `on_job_end`, so it was live across the whole
`on_job_start`→`on_job_end` span — a user-supplied `on_job_start` or
`on_job_end` hook could resolve straight from
`ctx[_CHILD_CONTAINER_KEY]`. That window is gone: the child is now open only
inside an `@inject` wrapper's call. `_CHILD_CONTAINER_KEY` was always a
private key, and the supported way to resolve a per-job dependency is
`@inject`, not reading the hook's `ctx` directly.

## Resolution

`FromDI` is `modern_di.integrations.from_di` — its marker factory. Calling
`FromDI(dependency)` returns an inert `Marker(dependency)` wrapping a
provider or a bare type; it does nothing on its own. Parameters opt into
injection by annotating them `typing.Annotated[SomeType, FromDI(dependency)]`.

`inject`:

1. `integrations.parse_markers(func)` scans the resolved type hints
   (`typing.get_type_hints(func, include_extras=True)`) for `Annotated`
   parameters carrying a `Marker`.
2. If none are found, `func` is returned unchanged — `inject` short-circuits
   without building a wrapper.
3. Otherwise it walks `inspect.signature(func).parameters` and raises
   `TypeError` **at decoration time** if any parameter is `VAR_POSITIONAL` or
   `VAR_KEYWORD` (`*args`/`**kwargs`) — see below for why.
4. It computes `visible_signature`: the original signature with the `FromDI`
   parameters dropped (everything else, including `ctx` and the real enqueued
   args, stays).
5. It builds an `async def wrapper(*args, **kwargs)` decorated with
   `functools.wraps(func)`. At call time the wrapper reads `ctx = args[0]`
   (arq always calls a task as `coroutine(ctx, *args, **kwargs)`), reads the
   per-job child off `ctx[_CHILD_CONTAINER_KEY]`, resolves every `FromDI`
   dependency via `integrations.resolve_markers(child, di_params)` — which
   calls each `Marker.resolve(container)`, itself
   `container.resolve_dependency(...)`, dispatching to `resolve_provider`
   for a provider instance and to `resolve` (by type) otherwise — binds the
   incoming `args`/`kwargs` against
   `visible_signature` (`bind` + `apply_defaults`), and calls `func` with the
   bound arguments plus the resolved ones merged in by name. Injection is
   therefore **order-insensitive**: a `FromDI` parameter may appear anywhere
   in the original signature, including before real positional args.

### Why no signature rewrite, and why `functools.wraps` is safe here

Unlike the Celery/aiogram integrations, `inject` never rewrites
`wrapper.__signature__`. arq performs no signature binding of a task at
all — the only `inspect.signature` call in arq's worker is on the `Worker`
class itself, never on a task — so there is nothing in arq to keep in sync
with a stripped signature. The only thing arq reads off a task callable is
`coroutine.__qualname__`, used as the default job name; `functools.wraps`
preserves that, so it is safe to use (it would not be, if arq unwrapped
`__wrapped__` to bind against the original signature the way aiogram does).

### Why the wrapper must be `async def`

arq's dispatch calls `inspect.iscoroutinefunction(coroutine_)` and raises if
false. `functools.wraps` copies metadata but does not change whether a
function is a genuine coroutine function, so `wrapper` must itself be
declared `async def` — which it is.

### The `*args`/`**kwargs` fail-fast guard

`inspect.Signature.bind` stores catch-all parameters under the literal names
`"args"`/`"kwargs"` in `bound.arguments`. The wrapper calls
`func(**bound.arguments, **resolved)` — a plain keyword unpacking — so a
`*args`/`**kwargs` parameter would silently arrive as a single keyword
argument literally named `args` or `kwargs` instead of being splatted back
into the call, breaking the task at the first invocation in a way not caught
by decoration itself. `inject` instead raises `TypeError` immediately when
decorating a task that combines a `FromDI` parameter with `*args`/`**kwargs`,
naming the offending parameter and the task's `__qualname__`. A task in this
shape has to spell its dependencies as explicit named parameters instead.

## Fully async, no auto-inject, no connection provider

Every lifecycle step is async (`open()` is sync — a `Container` is
constructed already open — but `close_async()`, `build_child_container` +
its own `close_async()`, and all four wrapped hooks are `async`), matching
arq itself being an async worker. There is no auto-inject path: arq's
`WorkerSettings.functions` list is heterogeneous (plain coroutines,
`arq.func(...)` wrappers, import strings), so blanket wrapping every entry is
out of scope — `@inject` is applied per task explicitly. There is also no
connection `ContextProvider`: arq's per-job `ctx` is a bare
`dict[str, Any]` (`job_id`, `job_try`, `enqueue_time`, `score`, `redis`, …),
not an injectable connection object — a task that needs job metadata reads
its `ctx` argument directly, the same shape as the Celery and Typer
integrations.
