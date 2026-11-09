"""Here am I: the message an ushabti answers with."""

from datetime import datetime

import pydantic

from ushabti._base import WireModel, utc_now
from ushabti.config import Mode


class HereAmI(WireModel):
    """The health signal and acknowledgement of one ushabti.

    It is the body of the health endpoint, and it is what an ushabti sends to
    its overseer to register and to stay registered. It carries the fields and
    implements of the spell so the overseer can route a summons (a unit of
    work) without holding the spell itself.
    """

    name: str
    """Name of the ushabti."""

    spell: str
    """Name of the spell it runs."""

    mode: Mode
    """Topology it runs in: `solo`, `independent`, or `overseen`."""

    halted: bool
    """True when the ushabti is halted: it observes but does not act."""

    last_vigil: datetime | None = None
    """When the last vigil tick finished, or None before the first one."""

    fields: tuple[str, ...] = ()
    """Names of the fields the spell covers."""

    implements: tuple[str, ...] = ()
    """Names of the implements the spell may use."""

    url: str | None = None
    """Where the overseer can reach this ushabti."""

    version: str | None = None
    """Version of the agent runtime."""

    sent_at: datetime = pydantic.Field(default_factory=utc_now)
    """When this message was produced."""
