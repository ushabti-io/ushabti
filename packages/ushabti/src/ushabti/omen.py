"""An omen: a prediction that Ma'at (the desired state) will be broken soon."""

from datetime import datetime

from pydantic import JsonValue

from ushabti._base import WireModel


class Omen(WireModel):
    """A prediction that Ma'at will be broken soon.

    Examples are a disk filling up or a certificate nearing expiry. Nothing is
    wrong yet, which is what separates an omen from isfet.
    """

    id: str
    """Identifier of this omen."""

    assertion: str
    """Id of the Ma'at assertion expected to break."""

    field: str | None = None
    """Name of the field the omen concerns, when known."""

    description: str
    """What the assertion says healthy looks like."""

    observed: JsonValue = None
    """The observed value that triggered the warning."""

    warning: str
    """The warning condition that was met, in readable form."""

    predicted_at: datetime
    """When the omen was raised."""
