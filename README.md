# modern-di-arq

[Modern-DI](https://github.com/modern-python/modern-di) integration for [arq](https://arq-docs.helpmanual.io).

## Install

```bash
pip install modern-di-arq
```

## Quickstart

```python
import typing

from modern_di import Container, Group, Scope, providers
from modern_di_arq import FromDI, inject, setup_di


class Settings:
    def __init__(self) -> None:
        self.greeting = "hello"


class Dependencies(Group):
    settings = providers.Factory(scope=Scope.APP, creator=Settings)


@inject
async def greet(ctx: dict, name: str, settings: typing.Annotated[Settings, FromDI(Dependencies.settings)]) -> str:
    return f"{settings.greeting}, {name}"


class WorkerSettings:
    functions = [greet]


setup_di(WorkerSettings, Container(groups=[Dependencies], validate=True))
```

`setup_di` wires the root container's lifecycle to arq's `on_startup`/`on_shutdown`
and seeds it into arq's `ctx` dict; a per-job `Scope.REQUEST` child is built in
`on_job_start` and closed in `on_job_end`. Decorate individual tasks with
`@inject` to resolve `FromDI` markers from that child. See the
[documentation](https://modern-di.modern-python.org).
