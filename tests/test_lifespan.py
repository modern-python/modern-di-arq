import typing
import warnings

import pytest
from arq.connections import RedisSettings
from arq.worker import Worker
from modern_di import Container, exceptions

import modern_di_arq
from modern_di_arq import fetch_di_container, setup_di
from tests.conftest import REDIS_URL, run_burst_worker
from tests.dependencies import Dependencies, app_teardowns, request_teardowns


async def resolve_job(ctx: dict[str, typing.Any]) -> None:
    child = ctx["modern_di_request_container"]
    child.resolve_dependency(Dependencies.request_factory)  # REQUEST-scoped (child)


def make_settings(container: Container) -> type:
    class WorkerSettings:
        functions: typing.ClassVar[list] = [resolve_job]

    setup_di(WorkerSettings, container)
    return WorkerSettings


def test_setup_di_returns_the_container() -> None:
    container = Container(groups=[Dependencies], validate=True)

    class WorkerSettings:
        functions: typing.ClassVar[list] = []

    assert modern_di_arq.setup_di(WorkerSettings, container) is container


def test_fetch_di_container_reads_root_from_ctx() -> None:
    container = Container(groups=[Dependencies], validate=True)
    ctx = {"modern_di_container": container}
    assert fetch_di_container(ctx) is container


def test_setup_di_rejects_double_call() -> None:
    container = Container(groups=[Dependencies], validate=True)

    class WorkerSettings:
        functions: typing.ClassVar[list] = []

    setup_di(WorkerSettings, container)
    with pytest.raises(TypeError, match="setup_di has already been called"):
        setup_di(WorkerSettings, container)


async def test_worker_runs_startup_job_and_shutdown(arq_redis) -> None:  # noqa: ANN001
    app_teardowns.clear()
    request_teardowns.clear()
    container = Container(groups=[Dependencies], validate=True)
    settings = make_settings(container)

    await arq_redis.enqueue_job("resolve_job")
    await run_burst_worker(settings)

    # per-job child was built and closed (REQUEST finalizer ran on on_job_end)
    assert request_teardowns == ["request-closed"]
    # resolving request_factory also resolves its app_resource dependency (APP-scoped),
    # which caches into the root container, so the APP finalizer runs on shutdown too
    assert app_teardowns == ["app-closed"]
    # root closed on shutdown
    assert container.closed is True


async def test_restart_reopens_without_warning(arq_redis) -> None:  # noqa: ANN001, ARG001
    container = Container(groups=[Dependencies], validate=True)
    settings = make_settings(container)

    await run_burst_worker(settings)  # first cycle: opens then closes the root
    assert container.closed is True

    with warnings.catch_warnings():
        warnings.simplefilter("error", exceptions.ContainerClosedWarning)
        await run_burst_worker(settings)  # second cycle must reopen without warning
    assert container.closed is True


async def test_setup_di_supports_dict_settings(arq_redis) -> None:  # noqa: ANN001
    container = Container(groups=[Dependencies], validate=True)
    settings: dict[str, typing.Any] = {"functions": [resolve_job]}
    setup_di(settings, container)

    await arq_redis.enqueue_job("resolve_job")
    worker_kwargs: dict[str, typing.Any] = {
        **settings,
        "redis_settings": RedisSettings.from_dsn(REDIS_URL),
        "burst": True,
        "handle_signals": False,
        "poll_delay": 0.01,
    }
    worker = Worker(**worker_kwargs)
    await worker.main()
    await worker.close()
    assert container.closed is True


async def test_setup_di_composes_with_user_hooks(arq_redis) -> None:  # noqa: ANN001
    calls: list[str] = []
    container = Container(groups=[Dependencies], validate=True)

    async def user_startup(ctx: dict[str, typing.Any]) -> None:
        calls.append("user_startup")
        assert fetch_di_container(ctx).closed is False  # our on_startup already opened the root

    async def user_job_start(ctx: dict[str, typing.Any]) -> None:
        calls.append("user_job_start")
        assert ctx["modern_di_request_container"].closed is False  # our on_job_start already built the child

    async def user_shutdown(ctx: dict[str, typing.Any]) -> None:
        calls.append("user_shutdown")
        assert fetch_di_container(ctx).closed is False  # our on_shutdown closes the root after the user's hook

    async def user_job_end(ctx: dict[str, typing.Any]) -> None:
        calls.append("user_job_end")
        # our on_job_end closes the child after the user's hook, so it's still live here
        assert ctx["modern_di_request_container"].closed is False

    class WorkerSettings:
        functions: typing.ClassVar[list] = [resolve_job]
        on_startup = user_startup
        on_shutdown = user_shutdown
        on_job_start = user_job_start
        on_job_end = user_job_end

    setup_di(WorkerSettings, container)
    await arq_redis.enqueue_job("resolve_job")
    await run_burst_worker(WorkerSettings)

    assert calls == ["user_startup", "user_job_start", "user_job_end", "user_shutdown"]
