"""The workforce registry: which ushabti exist and when each last answered."""

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ushabti import HereAmI, Mode
from ushabti_overseer.decade import DEFAULT_DECADE_SIZE, Decade, Decades

DEFAULT_MISSED_LIMIT = 3


class UshabtiStatus(StrEnum):
    """How an ushabti looks from the overseer."""

    ACTIVE = "active"
    HALTED = "halted"
    LOST = "lost"


class WorkforceMember(BaseModel):
    """One ushabti as the overseer knows it."""

    model_config = ConfigDict(frozen=True)

    name: str
    spell: str
    decade: int
    status: UshabtiStatus
    last_here_am_i: datetime
    mode: Mode
    fields: tuple[str, ...] = ()
    implements: tuple[str, ...] = ()
    url: str | None = None
    version: str | None = None

    def covers(self, field: str | None, implement: str | None) -> bool:
        """Whether this ushabti's spell covers the field and may use the implement."""
        return (field is None or field in self.fields) and (
            implement is None or implement in self.implements
        )


class WorkforceRegistry(Protocol):
    """Where the overseer keeps the workforce.

    The scaffold ships an in-memory registry. Anything with these methods can
    replace it, for example one backed by a database.
    """

    def record(self, message: HereAmI, at: datetime) -> WorkforceMember:
        """Register an ushabti, or note that a known one answered again."""
        ...

    def members(self, at: datetime) -> tuple[WorkforceMember, ...]:
        """Every ushabti, with its status as of ``at``."""
        ...

    def get(self, name: str, at: datetime) -> WorkforceMember | None: ...

    def decades(self) -> tuple[Decade, ...]: ...


class InMemoryWorkforceRegistry:
    """A registry that lives in the overseer's memory and is gone on restart.

    An ushabti is ``lost`` once more than ``missed_limit`` of its expected
    "Here am I" messages have failed to arrive. It becomes ``active`` again
    the moment it answers.
    """

    def __init__(
        self,
        *,
        decade_size: int = DEFAULT_DECADE_SIZE,
        here_am_i_interval: float = 30.0,
        missed_limit: int = DEFAULT_MISSED_LIMIT,
    ) -> None:
        if here_am_i_interval <= 0:
            raise ValueError("here_am_i_interval must be greater than zero")
        if missed_limit < 1:
            raise ValueError("missed_limit must be at least 1")
        self._decades = Decades(decade_size)
        self._patience = timedelta(seconds=here_am_i_interval * missed_limit)
        self._last: dict[str, tuple[HereAmI, datetime]] = {}

    def record(self, message: HereAmI, at: datetime) -> WorkforceMember:
        self._decades.assign(message.name)
        self._last[message.name] = (message, at)
        return self._member(message, at, at)

    def members(self, at: datetime) -> tuple[WorkforceMember, ...]:
        return tuple(
            self._member(message, seen, at)
            for message, seen in sorted(self._last.values(), key=_by_name)
        )

    def get(self, name: str, at: datetime) -> WorkforceMember | None:
        known = self._last.get(name)
        return None if known is None else self._member(known[0], known[1], at)

    def decades(self) -> tuple[Decade, ...]:
        return self._decades.all()

    def _member(self, message: HereAmI, seen: datetime, at: datetime) -> WorkforceMember:
        if at - seen > self._patience:
            status = UshabtiStatus.LOST
        elif message.halted:
            status = UshabtiStatus.HALTED
        else:
            status = UshabtiStatus.ACTIVE
        return WorkforceMember(
            name=message.name,
            spell=message.spell,
            decade=self._decades.assign(message.name),
            status=status,
            last_here_am_i=seen,
            mode=message.mode,
            fields=message.fields,
            implements=message.implements,
            url=message.url,
            version=message.version,
        )


def _by_name(entry: tuple[HereAmI, datetime]) -> str:
    return entry[0].name
