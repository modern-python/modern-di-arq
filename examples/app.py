# Minimal modern-di + arq example.
# Run for real (needs a running arq worker + Redis):  arq examples.app.WorkerSettings
import dataclasses
import typing

from modern_di import Container, Group, Scope, providers

from modern_di_arq import FromDI, inject, setup_di


@dataclasses.dataclass(kw_only=True)
class Settings:
    greeting: str = "Hello"


@dataclasses.dataclass(kw_only=True)
class GreetingService:
    settings: Settings  # auto-injected by type

    def greet(self, name: str) -> str:
        return f"{self.settings.greeting}, {name}!"


class Dependencies(Group):
    settings = providers.Factory(scope=Scope.APP, creator=Settings)
    service = providers.Factory(scope=Scope.REQUEST, creator=GreetingService)


@inject
async def greet(
    ctx: dict[str, typing.Any],  # noqa: ARG001  # arq passes its context dict as the first argument
    name: str,
    service: typing.Annotated[GreetingService, FromDI(Dependencies.service)],
) -> str:
    return service.greet(name)


class WorkerSettings:
    functions: typing.ClassVar[list] = [greet]


container = Container(groups=[Dependencies])
setup_di(WorkerSettings, container)
container.validate()  # optional fail-fast; must come after setup_di registers its providers
