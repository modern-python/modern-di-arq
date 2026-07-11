import os
import typing

import pytest
from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from arq.worker import Worker, create_worker


REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")


@pytest.fixture
async def arq_redis() -> typing.AsyncIterator[ArqRedis]:
    settings = RedisSettings.from_dsn(REDIS_URL)
    pool = await create_pool(settings)
    try:
        await pool.ping()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover
        await pool.aclose()
        pytest.skip(f"Redis not available at {REDIS_URL}: {exc}")
    await pool.flushdb()
    yield pool
    await pool.flushdb()
    await pool.aclose()


async def run_burst_worker(worker_settings: typing.Any) -> None:  # noqa: ANN401
    """Run all queued jobs through a real burst Worker, then shut it down.

    The worker owns its own pool (via ``redis_settings``) so ``close()`` — which
    fires ``on_shutdown`` and closes that pool — never touches the test's
    ``arq_redis`` fixture pool.
    """
    worker: Worker = create_worker(
        worker_settings,
        redis_settings=RedisSettings.from_dsn(REDIS_URL),
        burst=True,
        handle_signals=False,
        poll_delay=0.01,
    )
    await worker.main()  # fires on_startup, runs jobs (on_job_start/on_job_end each)
    await worker.close()  # fires on_shutdown, closes the worker's own pool
