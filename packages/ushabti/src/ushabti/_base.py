"""Shared building blocks for the SDK's data models.

This module is private. Import the public models from ``ushabti`` instead.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer, StringConstraints


class StrictModel(BaseModel):
    """Base for models a person writes by hand, such as a spell.

    Unknown keys are an error, so a typo never passes silently.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, use_attribute_docstrings=True)


class WireModel(BaseModel):
    """Base for models sent between processes or written to Papyrus.

    Unknown keys are ignored, so an older reader can accept a newer writer.
    """

    model_config = ConfigDict(extra="ignore", frozen=True, use_attribute_docstrings=True)


Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$", max_length=63)]
"""A lowercase name in DNS label style: letters, digits, and inner hyphens."""


def utc_now() -> datetime:
    """The current time, always timezone-aware and in UTC."""
    return datetime.now(UTC)


def new_id() -> str:
    """A fresh random identifier."""
    return uuid.uuid4().hex


_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d)$")
_UNIT_SECONDS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def parse_duration(value: object) -> object:
    """Turn ``"60s"``, ``"5m"``, or a number of seconds into a timedelta."""
    if isinstance(value, bool):
        raise ValueError("expected a duration such as 60s, not a boolean")
    if isinstance(value, int | float):
        return timedelta(seconds=value)
    if isinstance(value, str):
        match = _DURATION.match(value.strip())
        if match is None:
            raise ValueError("expected a duration such as 500ms, 60s, 5m, 1h, or 1d")
        return timedelta(seconds=float(match.group(1)) * _UNIT_SECONDS[match.group(2)])
    return value


def format_duration(value: timedelta) -> str:
    """Render a timedelta in the same compact form ``parse_duration`` reads."""
    seconds = value.total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size and seconds % size == 0:
            return f"{int(seconds // size)}{unit}"
    if seconds == int(seconds):
        return f"{int(seconds)}s"
    return f"{round(seconds * 1000)}ms"


Duration = Annotated[
    timedelta,
    BeforeValidator(parse_duration),
    PlainSerializer(format_duration, return_type=str),
]
"""A timedelta written as a short string such as ``60s`` or ``5m``."""
