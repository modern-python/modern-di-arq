import dataclasses

from modern_di import Group, Scope, providers


app_teardowns: list[str] = []
request_teardowns: list[str] = []


@dataclasses.dataclass(kw_only=True, slots=True)
class AppResource:
    label: str


@dataclasses.dataclass(kw_only=True, slots=True)
class RequestResource:
    app_resource: AppResource


async def _close_app(_: AppResource) -> None:
    app_teardowns.append("app-closed")


async def _close_request(_: RequestResource) -> None:
    request_teardowns.append("request-closed")


class Dependencies(Group):
    app_factory = providers.Factory(
        creator=AppResource,
        kwargs={"label": "root"},
        cache=providers.CacheSettings(finalizer=_close_app),
    )
    request_factory = providers.Factory(
        scope=Scope.REQUEST,
        creator=RequestResource,
        bound_type=None,
        cache=providers.CacheSettings(finalizer=_close_request),
    )
