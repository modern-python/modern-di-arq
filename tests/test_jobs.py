import typing

import pytest
from modern_di import Container, Group, Scope, providers

from modern_di_arq import FromDI, inject, setup_di
from modern_di_arq.main import _CHILD_CONTAINER_KEY, _ROOT_CONTAINER_KEY, _wrap_job_end, _wrap_job_start
from tests.conftest import run_burst_worker
from tests.dependencies import (
    AppResource,
    Dependencies,
    RequestResource,
    app_teardowns,
    request_teardowns,
)


results: dict[str, typing.Any] = {}


@inject
async def resolves_app_and_request(
    ctx: dict[str, typing.Any],  # noqa: ARG001
    x: int,
    app_instance: typing.Annotated[AppResource, FromDI(AppResource)],
    request_instance: typing.Annotated[RequestResource, FromDI(Dependencies.request_factory)],
) -> None:
    results["app_ok"] = isinstance(app_instance, AppResource)
    results["request_ok"] = isinstance(request_instance, RequestResource)
    results["x"] = x
    results["linked"] = request_instance.app_resource is app_instance


@inject
async def di_param_before_real_arg(
    ctx: dict[str, typing.Any],  # noqa: ARG001
    app_instance: typing.Annotated[AppResource, FromDI(AppResource)],
    x: int,
) -> None:
    results["order_x"] = x
    results["order_label"] = app_instance.label


async def no_fromdi(ctx: dict[str, typing.Any], x: int) -> None:  # noqa: ARG001
    results["plain"] = x * 2


boom_teardowns: list[str] = []


class Boom(Group):
    resource = providers.Factory(
        scope=Scope.REQUEST,
        creator=AppResource,
        kwargs={"label": "x"},
        bound_type=None,
        cache=providers.CacheSettings(finalizer=lambda _: boom_teardowns.append("closed")),
    )


@inject
async def raiser(
    ctx: dict[str, typing.Any],  # noqa: ARG001
    _res: typing.Annotated[AppResource, FromDI(Boom.resource)],
) -> None:
    msg = "boom"
    raise ValueError(msg)


def build_settings(container: Container, functions: list) -> type:
    class WorkerSettings:
        pass

    WorkerSettings.functions = functions  # ty: ignore[unresolved-attribute]
    setup_di(WorkerSettings, container)
    return WorkerSettings


async def test_inject_resolves_app_and_request(arq_redis) -> None:  # noqa: ANN001
    results.clear()
    app_teardowns.clear()
    request_teardowns.clear()
    container = Container(groups=[Dependencies], validate=True)
    settings = build_settings(container, [resolves_app_and_request])

    await arq_redis.enqueue_job("resolves_app_and_request", 7)
    await run_burst_worker(settings)

    assert results == {"app_ok": True, "request_ok": True, "x": 7, "linked": True}
    assert request_teardowns == ["request-closed"]  # per-job child closed
    assert app_teardowns == ["app-closed"]  # APP finalizer ran on shutdown (app_factory was resolved)


async def test_inject_is_order_insensitive(arq_redis) -> None:  # noqa: ANN001
    results.clear()
    container = Container(groups=[Dependencies], validate=True)
    settings = build_settings(container, [di_param_before_real_arg])

    await arq_redis.enqueue_job("di_param_before_real_arg", 42)
    await run_burst_worker(settings)

    assert results == {"order_x": 42, "order_label": "root"}


async def test_inject_passthrough_without_fromdi(arq_redis) -> None:  # noqa: ANN001
    results.clear()
    assert inject(no_fromdi) is no_fromdi  # no FromDI param: returned unchanged, not wrapped
    container = Container(groups=[Dependencies], validate=True)
    settings = build_settings(container, [inject(no_fromdi)])  # inject returns func unchanged

    await arq_redis.enqueue_job("no_fromdi", 5)
    await run_burst_worker(settings)

    assert results == {"plain": 10}


async def test_inject_closes_child_on_task_error(arq_redis) -> None:  # noqa: ANN001
    boom_teardowns.clear()
    container = Container(groups=[Boom], validate=True)
    settings = build_settings(container, [raiser])

    await arq_redis.enqueue_job("raiser")
    await run_burst_worker(settings)  # arq catches the job error; on_job_end still closes the child

    assert boom_teardowns == ["closed"]


async def test_wrapper_guarantees_close_without_on_job_end() -> None:
    """The wrapper's own `finally` closes the child, so a skipped on_job_end leaks nothing."""
    results.clear()
    request_teardowns.clear()
    container = Container(groups=[Dependencies], validate=True)
    container.open()
    ctx: dict[str, typing.Any] = {_ROOT_CONTAINER_KEY: container}
    await _wrap_job_start(None)(ctx)
    child = ctx[_CHILD_CONTAINER_KEY]
    assert child.closed is True  # built unopened by on_job_start

    await resolves_app_and_request(ctx, 7)  # invoke the @inject wrapper directly; on_job_end never runs

    assert results == {"app_ok": True, "request_ok": True, "x": 7, "linked": True}
    assert child.closed is True  # wrapper opened it, then closed it in its own finally
    assert request_teardowns == ["request-closed"]


async def test_wrapper_closes_child_when_task_raises_without_on_job_end() -> None:
    """The wrapper's `finally` closes the child even when the task raises, without on_job_end."""
    boom_teardowns.clear()
    container = Container(groups=[Boom], validate=True)
    container.open()
    ctx: dict[str, typing.Any] = {_ROOT_CONTAINER_KEY: container}
    await _wrap_job_start(None)(ctx)
    child = ctx[_CHILD_CONTAINER_KEY]

    with pytest.raises(ValueError, match="boom"):
        await raiser(ctx)

    assert child.closed is True
    assert boom_teardowns == ["closed"]


nested_calls: dict[str, typing.Any] = {}


@inject
async def inner_task(
    ctx: dict[str, typing.Any],
    app_instance: typing.Annotated[AppResource, FromDI(AppResource)],
) -> AppResource:
    nested_calls["inner_child_closed_during_call"] = ctx[_CHILD_CONTAINER_KEY].closed
    return app_instance


@inject
async def outer_task(
    ctx: dict[str, typing.Any],
    request_instance: typing.Annotated[RequestResource, FromDI(Dependencies.request_factory)],
) -> None:
    nested_calls["request_ok"] = isinstance(request_instance, RequestResource)
    inner_result = await inner_task(ctx)
    nested_calls["inner_ok"] = isinstance(inner_result, AppResource)
    nested_calls["child_closed_after_inner_returns"] = ctx[_CHILD_CONTAINER_KEY].closed


async def test_nested_inject_inner_does_not_close_shared_child() -> None:
    """Only the outer (owning) wrapper closes the shared child; the inner call must not."""
    nested_calls.clear()
    request_teardowns.clear()
    container = Container(groups=[Dependencies], validate=True)
    container.open()
    ctx: dict[str, typing.Any] = {_ROOT_CONTAINER_KEY: container}
    await _wrap_job_start(None)(ctx)
    child = ctx[_CHILD_CONTAINER_KEY]

    await outer_task(ctx)

    assert nested_calls["request_ok"] is True
    assert nested_calls["inner_ok"] is True
    assert nested_calls["inner_child_closed_during_call"] is False
    assert nested_calls["child_closed_after_inner_returns"] is False  # inner did not close it
    assert child.closed is True  # outer, the owner, closed it once after returning
    assert request_teardowns == ["request-closed"]  # finalizer ran exactly once


async def test_on_job_end_safety_net_closes_a_still_open_child() -> None:
    """on_job_end is a safety net: it closes the child only when some non-@inject path left it open."""
    request_teardowns.clear()
    container = Container(groups=[Dependencies], validate=True)
    container.open()
    ctx: dict[str, typing.Any] = {_ROOT_CONTAINER_KEY: container}
    await _wrap_job_start(None)(ctx)
    child = ctx[_CHILD_CONTAINER_KEY]
    child.open()  # simulate a non-@inject task resolving directly, leaving the child open
    child.resolve_dependency(Dependencies.request_factory)

    await _wrap_job_end(None)(ctx)

    assert child.closed is True
    assert _CHILD_CONTAINER_KEY not in ctx
    assert request_teardowns == ["request-closed"]


def test_inject_rejects_var_positional_with_fromdi() -> None:
    async def bad_task(
        ctx: dict[str, typing.Any],  # noqa: ARG001
        app_instance: typing.Annotated[AppResource, FromDI(AppResource)],  # noqa: ARG001
        *args: int,
    ) -> None:
        results["never_called"] = args  # pragma: no cover

    with pytest.raises(TypeError):
        inject(bad_task)


def test_inject_rejects_var_keyword_with_fromdi() -> None:
    async def bad_task(
        ctx: dict[str, typing.Any],  # noqa: ARG001
        app_instance: typing.Annotated[AppResource, FromDI(AppResource)],  # noqa: ARG001
        **kwargs: int,
    ) -> None:
        results["never_called"] = kwargs  # pragma: no cover

    with pytest.raises(TypeError):
        inject(bad_task)
