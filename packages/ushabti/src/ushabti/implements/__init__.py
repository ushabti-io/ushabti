"""Implements: the tools an ushabti can use.

Every implement is either ``read`` or ``write``. A write implement only runs
when the weighing (the safety gate) returned ``PASS`` for it. That rule is
enforced here, in ``Implement.run``, so no caller can skip it by accident.
"""

import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, final

from pydantic import JsonValue

from ushabti._base import WireModel
from ushabti.errors import ImplementError, UnknownImplementError, UnweighedWriteError
from ushabti.field import Field

if TYPE_CHECKING:
    from ushabti.weighing import WeighingResult

IMPLEMENT_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
"""Implement names look like ``kubernetes.list_pods``: a namespace, a dot, an operation."""

NO_PARAMETERS: Final[Mapping[str, JsonValue]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class ImplementKind(StrEnum):
    """Whether an implement only observes or can change something."""

    READ = "read"
    WRITE = "write"


class ImplementSpec(WireModel):
    """What a model is told about an implement."""

    name: str
    kind: ImplementKind
    description: str
    parameters: dict[str, JsonValue]


class Implement(ABC):
    """A tool an ushabti can use.

    Subclasses set ``name``, ``kind``, ``description``, and ``parameters`` (a
    JSON Schema for the arguments), and implement ``_run``. Callers use
    ``run``, which checks the verdict first.

    This is an abstract base class and not a structural protocol on purpose:
    the verdict check lives in ``run``, and only inheritance guarantees that
    every implement carries it.
    """

    name: ClassVar[str]
    kind: ClassVar[ImplementKind]
    description: ClassVar[str]
    parameters: ClassVar[Mapping[str, JsonValue]] = NO_PARAMETERS

    @final
    async def run(
        self,
        arguments: Mapping[str, JsonValue],
        *,
        field: Field | None = None,
        weighed: "WeighingResult | None" = None,
    ) -> JsonValue:
        """Run the implement, refusing unless the weighing allows it.

        A write implement needs a ``PASS`` verdict naming this implement. A
        read implement may run without one, which is how the vigil observes,
        but when a verdict is given it must also be ``PASS``.
        """
        self._require_pass(weighed)
        return await self._run(arguments, field)

    @abstractmethod
    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        """Do the work. Never call this directly: go through ``run``."""

    def spec(self) -> ImplementSpec:
        """The description of this implement that a model sees."""
        return ImplementSpec(
            name=self.name,
            kind=self.kind,
            description=self.description,
            parameters=dict(self.parameters),
        )

    def _require_pass(self, weighed: "WeighingResult | None") -> None:
        # Imported here because weighing imports this module for ImplementKind.
        from ushabti.weighing import Verdict

        if weighed is None:
            if self.kind is ImplementKind.READ:
                return
            raise UnweighedWriteError(
                f"{self.name} is a write implement and was not weighed; "
                "a write only runs with a PASS verdict"
            )
        if weighed.implement != self.name:
            raise UnweighedWriteError(
                f"the verdict is for {weighed.implement}, not for {self.name}"
            )
        if weighed.verdict is not Verdict.PASS:
            raise UnweighedWriteError(
                f"{self.name} was weighed {weighed.verdict.value}; only PASS may run"
            )


class ImplementRegistry:
    """The implements a process knows about, by name."""

    def __init__(self, implements: Iterable[Implement] = ()) -> None:
        self._implements: dict[str, Implement] = {}
        for implement in implements:
            self.register(implement)

    def register(self, implement: Implement) -> None:
        """Add an implement. Its name must be well formed and not taken."""
        name = getattr(implement, "name", None)
        if not isinstance(name, str) or IMPLEMENT_NAME.match(name) is None:
            raise ValueError(
                f"implement name {name!r} must look like namespace.operation, "
                "in lowercase letters, digits, and underscores"
            )
        if not isinstance(getattr(implement, "kind", None), ImplementKind):
            raise ValueError(f"implement {name} must declare kind as read or write")
        if not getattr(implement, "description", ""):
            raise ValueError(f"implement {name} must have a description")
        if name in self._implements:
            raise ValueError(f"implement {name} is already registered")
        self._implements[name] = implement

    def get(self, name: str) -> Implement:
        """The implement with this name, or UnknownImplementError."""
        try:
            return self._implements[name]
        except KeyError:
            raise UnknownImplementError(name) from None

    def find(self, name: str) -> Implement | None:
        """The implement with this name, or None."""
        return self._implements.get(name)

    def kind_of(self, name: str) -> ImplementKind | None:
        """The kind of the named implement, or None when it is not registered."""
        implement = self._implements.get(name)
        return None if implement is None else implement.kind

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._implements))

    def __contains__(self, name: object) -> bool:
        return name in self._implements

    def __iter__(self) -> Iterator[Implement]:
        return iter(self._implements.values())

    def __len__(self) -> int:
        return len(self._implements)


def text_argument(implement: str, arguments: Mapping[str, JsonValue], key: str) -> str:
    """A required text argument. Numbers are accepted and written out; anything else is refused."""
    value = optional_text_argument(implement, arguments, key)
    if value is None:
        raise ImplementError(f"{implement}: the argument '{key}' is required")
    return value


def optional_text_argument(
    implement: str, arguments: Mapping[str, JsonValue], key: str
) -> str | None:
    """An optional text argument: None when absent, ImplementError when it is not text."""
    value = arguments.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        raise ImplementError(f"{implement}: the argument '{key}' must be a string")
    return str(value)


def builtin_implements() -> tuple[Implement, ...]:
    """Fresh instances of every implement that ships with the SDK."""
    # Imported here because these modules import Implement from this one.
    from ushabti.implements import kubernetes, noop, prometheus, proxmox

    return (
        *noop.implements(),
        *kubernetes.implements(),
        *proxmox.implements(),
        *prometheus.implements(),
    )


def default_registry() -> ImplementRegistry:
    """A new registry holding the built-in implements."""
    return ImplementRegistry(builtin_implements())
