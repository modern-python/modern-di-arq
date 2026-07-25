import typing

from examples.app import container, greet
from modern_di_arq.main import _ROOT_CONTAINER_KEY, _wrap_job_start


async def test_example_resolves_and_greets() -> None:
    container.open()
    ctx: dict[str, typing.Any] = {_ROOT_CONTAINER_KEY: container}
    await _wrap_job_start(None)(ctx)  # seeds the per-job REQUEST child, mirroring on_job_start

    result = await greet(ctx, "world")

    assert result == "Hello, world!"
    await container.close_async()
