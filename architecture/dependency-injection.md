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
`on_job_end`. Two named constants hand the container through it, so the
writer and every reader stay in provable agreement instead of relying on bare
string literals:

- `_ROOT_CONTAINER_KEY = "modern_di_container"` — the root container, seeded
  into the worker `ctx` by `setup_di` and read back by `fetch_di_container`
  and by `on_job_start` (to build the per-job child).
- `_CHILD_CONTAINER_KEY = "modern_di_request_container"` — the per-job
  `Scope.REQUEST` child, stashed on the per-job `ctx` by `on_job_start`, read
  by `inject`'s wrapper, and closed by `on_job_end`.

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
- `on_job_start`: the `Scope.REQUEST` child is built, *then* the existing
  hook (the user hook can resolve from the child).
- `on_job_end`: the existing hook runs *first*, *then* the child is closed
  (the user hook still has a live child).
- `on_shutdown`: the existing hook runs *first*, *then* `container.close_async()`
  (the user hook still has an open root).

`on_job_end` pops the child with `ctx.pop(_CHILD_CONTAINER_KEY, None)` and is
a no-op if absent — defensive only; arq always pairs `on_job_start`/`on_job_end`
around a job, including the error path (arq catches a task's exception itself
and still fires `on_job_end`).

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
`root.build_child_container(scope=Scope.REQUEST)` — and stashes it under
`_CHILD_CONTAINER_KEY` on that job's `ctx`. `on_job_end` closes it with
`close_async()`, unconditionally: because the compose ordering runs the
user's `on_job_end` first and the close second, the child is torn down
whether the task raised or returned, and it happens even for a task that was
never decorated with `@inject` (a child is built for every job regardless of
whether anything reads it back — see the non-goals note in the design; not
optimized).

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
