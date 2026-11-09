"""Helpers shared by the overseer tests."""

from datetime import UTC, datetime, timedelta

from ushabti import HereAmI, Mode, Summons, SummonsSource

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
TOKEN = "test-admin-token"
BEARER = {"Authorization": f"Bearer {TOKEN}"}
JSON = {"Content-Type": "application/json"}
# What a well-behaved caller sends: the token, and a body declared as JSON.
AUTHED = {**BEARER, **JSON}


class Clock:
    """A clock that only moves when told to."""

    def __init__(self, now: datetime = AT) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def here_am_i(
    name: str = "k3s-watcher",
    *,
    fields: tuple[str, ...] = ("homelab-k3s",),
    implements: tuple[str, ...] = ("kubernetes.list_pods", "kubernetes.restart_deployment"),
    halted: bool = False,
    url: str | None = None,
) -> HereAmI:
    return HereAmI(
        name=name,
        spell=f"{name}-spell",
        mode=Mode.OVERSEEN,
        halted=halted,
        fields=fields,
        implements=implements,
        url=f"http://{name}.example.invalid:8080" if url is None else (url or None),
        sent_at=AT,
    )


def summons(
    field: str | None = "homelab-k3s",
    implement: str | None = "kubernetes.restart_deployment",
    summons_id: str = "summons-1",
) -> Summons:
    return Summons(
        id=summons_id,
        source=SummonsSource.OPERATOR,
        reason="Restart the web deployment.",
        field=field,
        implement=implement,
        created_at=AT,
    )
