"""Implements that touch nothing. For tests and examples."""

from collections.abc import Mapping
from typing import ClassVar

from pydantic import JsonValue

from ushabti.field import Field
from ushabti.implements import Implement, ImplementKind

_ANY_ARGUMENTS: Mapping[str, JsonValue] = {"type": "object", "additionalProperties": True}


class NoopRead(Implement):
    """A read implement that returns its own arguments as the observation.

    That makes a Ma'at assertion testable from a spell alone: the spell decides
    what the "observation" is, and no real system is involved.
    """

    name: ClassVar[str] = "noop.read"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Return the given arguments unchanged. Observes nothing real."
    parameters: ClassVar[Mapping[str, JsonValue]] = _ANY_ARGUMENTS

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        return dict(arguments)


class NoopWrite(Implement):
    """A write implement that changes nothing and remembers each call.

    ``calls`` lets a test prove that a write did or did not execute.
    """

    name: ClassVar[str] = "noop.write"
    kind: ClassVar[ImplementKind] = ImplementKind.WRITE
    description: ClassVar[str] = "Pretend to change something. Changes nothing real."
    parameters: ClassVar[Mapping[str, JsonValue]] = _ANY_ARGUMENTS

    def __init__(self) -> None:
        self.calls: list[dict[str, JsonValue]] = []

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        self.calls.append(dict(arguments))
        return {"ok": True, "arguments": dict(arguments)}


def implements() -> tuple[Implement, ...]:
    return (NoopRead(), NoopWrite())
