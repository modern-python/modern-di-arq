<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)"  srcset="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/modern-di-arq/lockup-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/modern-di-arq/lockup-light.svg">
    <img alt="modern-di-arq" src="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/modern-di-arq/lockup.png" width="420">
  </picture>
</p>

[![PyPI version](https://img.shields.io/pypi/v/modern-di-arq.svg)](https://pypi.org/project/modern-di-arq/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/modern-di-arq.svg)](https://pypi.org/project/modern-di-arq/)
[![Downloads](https://static.pepy.tech/badge/modern-di-arq/month)](https://pepy.tech/projects/modern-di-arq)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/modern-python/modern-di-arq/actions/workflows/ci.yml)
[![CI](https://github.com/modern-python/modern-di-arq/actions/workflows/ci.yml/badge.svg)](https://github.com/modern-python/modern-di-arq/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/modern-python/modern-di-arq.svg)](https://github.com/modern-python/modern-di-arq/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/modern-python/modern-di-arq)](https://github.com/modern-python/modern-di-arq/stargazers)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)

[Modern-DI](https://github.com/modern-python/modern-di) integration for [arq](https://arq-docs.helpmanual.io).

Full guide: [arq integration docs](https://modern-di.modern-python.org/integrations/arq/)

## Installation

```bash
uv add modern-di-arq      # or: pip install modern-di-arq
```

## Usage

`setup_di` seeds the root container into arq's `ctx` dict and wires four of arq's lifecycle hooks: `on_startup`/`on_shutdown` open and close the root container, and `on_job_start` builds a `Scope.REQUEST` child container per job. Decorate a task with `@inject` to resolve its `FromDI`-marked parameters from that per-job child — the child is only open for the duration of an `@inject`-decorated task's body, which owns opening and closing it, guaranteeing teardown even if arq skips `on_job_end`.

```python
import typing

from arq.connections import RedisSettings
from modern_di import Container, Group, Scope, providers
from modern_di_arq import FromDI, inject, setup_di


class Settings:
    def __init__(self) -> None:
        self.greeting = "hello"


class Greeter:
    def __init__(self, settings: Settings) -> None:   # auto-injected by type
        self._settings = settings

    def greet(self, name: str) -> str:
        return f"{self._settings.greeting}, {name}"


class AppGroup(Group):
    settings = providers.Factory(Settings, scope=Scope.APP, cache=True)
    greeter = providers.Factory(Greeter, scope=Scope.REQUEST)


@inject
async def greet(
    ctx: dict[str, typing.Any],       # arq passes its context dict as the first argument
    name: str,
    greeter: typing.Annotated[Greeter, FromDI(Greeter)],   # resolve by type
) -> str:
    return greeter.greet(name)


class WorkerSettings:
    functions = [greet]
    redis_settings = RedisSettings(host="localhost")


setup_di(WorkerSettings, Container(groups=[AppGroup], validate=True))
```

Run the worker as usual (`arq mymodule.WorkerSettings`) and enqueue jobs with only their real arguments — `await pool.enqueue_job("greet", "world")` — the `FromDI` parameters are resolved for you. A task **must** declare arq's `ctx` dict as its first parameter; injection is order-insensitive otherwise. arq's `ctx` is a plain `dict` (not a dedicated message type), so no context provider is registered — read job metadata from `ctx`, and `fetch_di_container(ctx)` returns the root container.

## API

| Symbol | Description |
|---|---|
| `setup_di(worker_settings, container)` | Seeds the root container into arq's `ctx` and wires root + per-job lifecycle onto `on_startup`/`on_shutdown`/`on_job_start`/`on_job_end`. Accepts a `WorkerSettings` class/object or a settings `dict`; composes with existing hooks; returns the container. Raises `TypeError` if called twice on the same `worker_settings` |
| `FromDI(dependency)` | Inert marker for `Annotated[T, FromDI(...)]` in task signatures; accepts a provider instance or a type |
| `inject(task)` | Decorator that resolves `FromDI`-annotated parameters from the per-job `Scope.REQUEST` child. Order-insensitive; passthrough for tasks with no `FromDI`; raises `TypeError` at decoration if the task also declares `*args`/`**kwargs` |
| `fetch_di_container(ctx)` | Returns the root container from an arq `ctx` dict |

## 📦 [PyPI](https://pypi.org/project/modern-di-arq)

## 📝 [License](LICENSE)

## Part of `modern-python`

Built on [`modern-di`](https://github.com/modern-python/modern-di), a dependency-injection framework with IoC container and scopes.

Browse the full list of templates and libraries in
[`modern-python`](https://github.com/modern-python) — see the org profile for the categorized index.
