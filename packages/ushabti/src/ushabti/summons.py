"""A summons: a unit of work for an ushabti."""

from datetime import datetime
from enum import StrEnum
from typing import Self

import pydantic

from ushabti._base import WireModel, new_id, utc_now


class SummonsSource(StrEnum):
    """Where a summons came from."""

    VIGIL = "vigil"
    OVERSEER = "overseer"
    OPERATOR = "operator"


class SummonsState(StrEnum):
    """How far an ushabti has got with a summons."""

    PENDING = "pending"
    """Accepted and waiting."""

    ANSWERING = "answering"
    """The ushabti is working on it."""

    ANSWERED = "answered"
    """Finished. Every proposed implement call ran or was recorded as a dry run."""

    ESCALATED = "escalated"
    """Finished, but at least one call needs operator approval."""

    FAILED = "failed"
    """Could not be answered, for example because no model is configured."""


class Summons(WireModel):
    """A unit of work. It comes from the vigil, an overseer, or a human operator."""

    id: str = pydantic.Field(default_factory=new_id)
    """Identifier of the summons. It stays the same when an overseer routes it."""

    source: SummonsSource
    """Where the summons came from: `vigil`, `overseer`, or `operator`."""

    reason: str
    """What needs doing, in plain language. The model reads this."""

    field: str | None = None
    """Name of the field the work concerns."""

    implement: str | None = None
    """Name of an implement the work is expected to need. The overseer routes on it."""

    cause: str | None = None
    """Id of the isfet or omen that raised this summons, if any."""

    state: SummonsState = SummonsState.PENDING
    """How far the ushabti has got with it."""

    detail: str | None = None
    """A short note on the outcome."""

    created_at: datetime = pydantic.Field(default_factory=utc_now)
    """When the summons was created."""

    def moved_to(self, state: SummonsState, detail: str | None = None) -> Self:
        """A copy of this summons in a new state."""
        return self.model_copy(update={"state": state, "detail": detail})
