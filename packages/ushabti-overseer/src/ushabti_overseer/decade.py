"""Decades: groups of ushabti supervised by one overseer."""

from pydantic import BaseModel, ConfigDict

DEFAULT_DECADE_SIZE = 10


class Decade(BaseModel):
    """A group of ushabti. Ten by default, after the historical ratio."""

    model_config = ConfigDict(frozen=True)

    number: int
    members: tuple[str, ...]


class Decades:
    """Assigns each ushabti to a decade and remembers the assignment.

    A new ushabti joins the first decade with room. When every decade is full,
    it starts a new one. An ushabti keeps its decade for as long as the
    overseer runs, whether or not it is currently answering.
    """

    def __init__(self, size: int = DEFAULT_DECADE_SIZE) -> None:
        if size < 1:
            raise ValueError("a decade must hold at least one ushabti")
        self.size = size
        self._decades: list[list[str]] = []
        self._assigned: dict[str, int] = {}

    def assign(self, name: str) -> int:
        """The decade number of this ushabti, assigning one on first sight."""
        known = self._assigned.get(name)
        if known is not None:
            return known
        for number, members in enumerate(self._decades, start=1):
            if len(members) < self.size:
                members.append(name)
                self._assigned[name] = number
                return number
        self._decades.append([name])
        self._assigned[name] = len(self._decades)
        return len(self._decades)

    def of(self, name: str) -> int | None:
        """The decade number of this ushabti, or None when it has never been seen."""
        return self._assigned.get(name)

    def all(self) -> tuple[Decade, ...]:
        return tuple(
            Decade(number=number, members=tuple(members))
            for number, members in enumerate(self._decades, start=1)
        )
