"""Routing: choosing which ushabti answers a summons."""

from collections.abc import Sequence

from ushabti import Summons
from ushabti_overseer.registry import UshabtiStatus, WorkforceMember


class NoRouteError(Exception):
    """No ushabti can take the summons. ``reason`` says why, in plain language."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class Router:
    """Routes a summons (a unit of work) to an ushabti whose spell covers it.

    A spell covers a summons when it lists the field the summons names and the
    implement the summons expects to need. A summons that names neither can go
    to any ushabti. Among those that qualify, the one routed to least recently
    goes first, so work spreads across the decade.
    """

    def __init__(self) -> None:
        self._turn = 0
        self._last_routed: dict[str, int] = {}

    def candidates(
        self, summons: Summons, members: Sequence[WorkforceMember]
    ) -> list[WorkforceMember]:
        """Every ushabti that can take the summons, best choice first.

        Raises NoRouteError with the reason when there is none.
        """
        if not members:
            raise NoRouteError("no ushabti is registered")

        covering = [m for m in members if m.covers(summons.field, summons.implement)]
        if not covering:
            raise NoRouteError(_nobody_covers(summons))

        active = [m for m in covering if m.status is UshabtiStatus.ACTIVE]
        if not active:
            states = ", ".join(f"{m.name} is {m.status.value}" for m in covering)
            raise NoRouteError(f"every ushabti that covers it is unavailable: {states}")

        reachable = [m for m in active if m.url]
        if not reachable:
            names = ", ".join(m.name for m in active)
            raise NoRouteError(f"no ushabti that covers it advertises a URL: {names}")

        return sorted(reachable, key=lambda m: (self._last_routed.get(m.name, 0), m.name))

    def delivered(self, member: WorkforceMember) -> None:
        """Note that this ushabti took a summons, so the next one goes elsewhere."""
        self._turn += 1
        self._last_routed[member.name] = self._turn


def _nobody_covers(summons: Summons) -> str:
    if summons.field and summons.implement:
        return f"no ushabti tends the field {summons.field} with the implement {summons.implement}"
    if summons.field:
        return f"no ushabti tends the field {summons.field}"
    return f"no ushabti may use the implement {summons.implement}"
