"""modern-di integration for arq.

The integration manipulates arq's ``ctx`` dict, its lifecycle hook callables,
and a settings class/dict structurally, so this module needs no arq import.
"""

import typing

from modern_di import Container, Scope


_ROOT_CONTAINER_KEY = "modern_di_container"
_CHILD_CONTAINER_KEY = "modern_di_request_container"


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
        ctx[_CHILD_CONTAINER_KEY] = root.build_child_container(scope=Scope.REQUEST)
        if existing is not None:
            await existing(ctx)

    return on_job_start


def _wrap_job_end(existing: _Hook | None) -> _Hook:
    async def on_job_end(ctx: dict[str, typing.Any]) -> None:
        if existing is not None:
            await existing(ctx)
        child = ctx.pop(_CHILD_CONTAINER_KEY, None)
        if child is not None:
            await child.close_async()  # never leak the per-job child, even on the error path

    return on_job_end


def setup_di(worker_settings: typing.Any, container: Container) -> Container:  # noqa: ANN401
    """Wire *container* into an arq worker.

    Seeds the root container into the worker ``ctx`` (arq's state store) and
    wraps the worker's lifecycle hooks: ``on_startup``/``on_shutdown`` open and
    close the root; ``on_job_start``/``on_job_end`` build and close a
    ``Scope.REQUEST`` child per job. Any hook the user already set still runs.
    Accepts a class/object ``worker_settings`` (attribute access) or a ``dict``
    (item access). Returns *container*.
    """
    ctx = _get_setting(worker_settings, "ctx") or {}
    ctx[_ROOT_CONTAINER_KEY] = container
    _set_setting(worker_settings, "ctx", ctx)

    _set_setting(worker_settings, "on_startup", _wrap_startup(container, _get_setting(worker_settings, "on_startup")))
    _set_setting(
        worker_settings, "on_shutdown", _wrap_shutdown(container, _get_setting(worker_settings, "on_shutdown"))
    )
    _set_setting(worker_settings, "on_job_start", _wrap_job_start(_get_setting(worker_settings, "on_job_start")))
    _set_setting(worker_settings, "on_job_end", _wrap_job_end(_get_setting(worker_settings, "on_job_end")))
    return container


def fetch_di_container(ctx: dict[str, typing.Any]) -> Container:
    """Read the root container back out of an arq ``ctx`` dict."""
    return typing.cast(Container, ctx[_ROOT_CONTAINER_KEY])
