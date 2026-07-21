"""modern-di integration for arq.

The integration manipulates arq's ``ctx`` dict, its lifecycle hook callables,
and a settings class/dict structurally, so this module needs no arq import.
"""

import functools
import inspect
import typing

from modern_di import Container, Scope, integrations


_ROOT_CONTAINER_KEY = "modern_di_container"
_CHILD_CONTAINER_KEY = "modern_di_request_container"
_CHILD_DEPTH_KEY = "modern_di_request_container_depth"
_WRAPPED_MARKER = "__modern_di_wrapped__"


def _get_setting(worker_settings: typing.Any, name: str) -> typing.Any:  # noqa: ANN401
    if isinstance(worker_settings, dict):
        return worker_settings.get(name)
    return getattr(worker_settings, name, None)


def _set_setting(worker_settings: typing.Any, name: str, value: typing.Any) -> None:  # noqa: ANN401
    if isinstance(worker_settings, dict):
        worker_settings[name] = value
    else:
        setattr(worker_settings, name, value)


_Hook = typing.Callable[[dict[str, typing.Any]], typing.Awaitable[None]]


def _wrap_startup(container: Container, existing: _Hook | None) -> _Hook:
    async def on_startup(ctx: dict[str, typing.Any]) -> None:
        container.open()  # reopen for restart / worker re-entry; no-op if already open
        if existing is not None:
            await existing(ctx)

    setattr(on_startup, _WRAPPED_MARKER, True)
    return on_startup


def _wrap_shutdown(container: Container, existing: _Hook | None) -> _Hook:
    async def on_shutdown(ctx: dict[str, typing.Any]) -> None:
        if existing is not None:
            await existing(ctx)
        await container.close_async()  # run APP-scoped finalizers

    return on_shutdown


def _wrap_job_start(existing: _Hook | None) -> _Hook:
    async def on_job_start(ctx: dict[str, typing.Any]) -> None:
        root = typing.cast(Container, ctx[_ROOT_CONTAINER_KEY])
        # Built unopened: `@inject`'s wrapper(s) refcount open/close (see `inject`),
        # so the child stays closed here even if a user hook resolves-then-raises below.
        child = root.build_child_container(scope=Scope.REQUEST)
        ctx[_CHILD_CONTAINER_KEY] = child
        if existing is not None:
            await existing(ctx)

    return on_job_start


def _wrap_job_end(existing: _Hook | None) -> _Hook:
    async def on_job_end(ctx: dict[str, typing.Any]) -> None:
        if existing is not None:
            await existing(ctx)
        child = ctx.pop(_CHILD_CONTAINER_KEY, None)
        ctx.pop(_CHILD_DEPTH_KEY, None)
        # Safety net only: normally the owning `@inject` wrapper(s) already closed the
        # child in their own `finally`, or it was never opened (no `@inject` task) —
        # both are no-ops here. Only closes if still open (e.g. a non-`@inject` task
        # left it open, or arq itself raised between the task and this hook).
        if child is not None and not child.closed:
            await child.close_async()

    return on_job_end


def setup_di(worker_settings: typing.Any, container: Container) -> Container:  # noqa: ANN401
    """Wire *container* into an arq worker.

    Seeds the root container into the worker ``ctx`` (arq's state store) and
    wraps the worker's lifecycle hooks: ``on_startup``/``on_shutdown`` open and
    close the root; ``on_job_start`` builds an unopened ``Scope.REQUEST`` child
    per job, opened and closed by ``@inject``-decorated task(s) around their
    own bodies — reference-counted, so nested and concurrent (``asyncio.gather``)
    ``@inject`` calls over the same job share one open child, closed exactly
    once by the last to exit (``on_job_end`` only closes it as a safety net).
    Any hook the user already set still runs. Accepts a class/object
    ``worker_settings`` (attribute access) or a ``dict`` (item access).
    Returns *container*.

    Raises:
        TypeError: *worker_settings* was already wired by a prior ``setup_di`` call.

    """
    existing_on_startup = _get_setting(worker_settings, "on_startup")
    if getattr(existing_on_startup, _WRAPPED_MARKER, False):
        msg = "setup_di has already been called on this worker_settings"
        raise TypeError(msg)

    ctx = _get_setting(worker_settings, "ctx") or {}
    ctx[_ROOT_CONTAINER_KEY] = container
    _set_setting(worker_settings, "ctx", ctx)

    _set_setting(worker_settings, "on_startup", _wrap_startup(container, existing_on_startup))
    _set_setting(
        worker_settings, "on_shutdown", _wrap_shutdown(container, _get_setting(worker_settings, "on_shutdown"))
    )
    _set_setting(worker_settings, "on_job_start", _wrap_job_start(_get_setting(worker_settings, "on_job_start")))
    _set_setting(worker_settings, "on_job_end", _wrap_job_end(_get_setting(worker_settings, "on_job_end")))
    return container


def fetch_di_container(ctx: dict[str, typing.Any]) -> Container:
    """Read the root container back out of an arq ``ctx`` dict."""
    return typing.cast(Container, ctx[_ROOT_CONTAINER_KEY])


T = typing.TypeVar("T")


FromDI = integrations.from_di


def inject(func: typing.Callable[..., typing.Awaitable[T]]) -> typing.Callable[..., typing.Awaitable[T]]:
    """Resolve ``FromDI`` params of an arq task from its per-job child container.

    arq calls the task as ``coroutine(ctx, *args, **kwargs)`` and never binds the
    task signature, so ``functools.wraps`` is safe and no signature rewrite is
    needed. Injection is parameter-order-insensitive (bind-by-name). A task with
    no ``FromDI`` parameter is returned unchanged.

    Raises:
        TypeError: ``func`` declares ``*args``/``**kwargs`` alongside a ``FromDI``
            parameter. ``inspect.Signature.bind`` stores those under the literal
            names ``"args"``/``"kwargs"``, which the wrapper's
            ``**bound.arguments`` unpacking would silently misroute; use explicit
            named parameters instead.

    """
    di_params = integrations.parse_markers(func)
    if not di_params:
        return func

    signature = inspect.signature(func)
    for name, param in signature.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            func_name = getattr(func, "__qualname__", repr(func))
            msg = (
                f"@inject task {func_name!r} declares *args/**kwargs (parameter {name!r}), "
                "which is unsupported; use explicit named parameters instead of *args/**kwargs with @inject."
            )
            raise TypeError(msg)

    visible_params = [p for name, p in signature.parameters.items() if name not in di_params]
    visible_signature = signature.replace(parameters=visible_params)

    @functools.wraps(func)
    async def wrapper(*args: typing.Any, **kwargs: typing.Any) -> T:  # noqa: ANN401
        ctx = typing.cast("dict[str, typing.Any]", args[0])
        child = typing.cast(Container, ctx[_CHILD_CONTAINER_KEY])
        # Reference-count opens so nested AND concurrent (@inject fan-out via gather)
        # share one open child and close it exactly once, when the LAST @inject body
        # exits. The check/open/increment run without an await, so asyncio cannot
        # interleave them — the count is consistent under concurrency.
        depth = ctx.get(_CHILD_DEPTH_KEY, 0)
        if depth == 0:
            child.open()
        ctx[_CHILD_DEPTH_KEY] = depth + 1
        try:
            resolved = integrations.resolve_markers(child, di_params)
            bound = visible_signature.bind(*args, **kwargs)
            bound.apply_defaults()
            return await func(**bound.arguments, **resolved)
        finally:
            # Guarantees teardown even if arq skips on_job_end (e.g. it fails to
            # serialize an unpicklable job result/exception after the task runs).
            ctx[_CHILD_DEPTH_KEY] -= 1
            if ctx[_CHILD_DEPTH_KEY] == 0:
                await child.close_async()

    return wrapper
