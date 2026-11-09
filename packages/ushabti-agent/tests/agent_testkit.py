"""Helpers shared by the agent tests."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Any, ClassVar

import yaml
from pydantic import JsonValue

from ushabti import (
    Completion,
    Field,
    Implement,
    ImplementKind,
    ImplementRegistry,
    JsonlPapyrus,
    ModelProvider,
    PapyrusEntry,
    Spell,
    StopReason,
    ToolCall,
    Weighing,
)
from ushabti.implements.noop import NoopRead, NoopWrite
from ushabti_agent.runtime import Ushabti

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

UNHEALTHY: dict[str, Any] = {
    "id": "service-healthy",
    "description": "The service reports healthy.",
    "observe": {"implement": "noop.read", "arguments": {"healthy": False}},
    "expect": {"path": "healthy", "op": "eq", "value": True},
}

LOOSE: dict[str, Any] = {"dry_run": False, "require_approval_for": [], "max_actions_per_hour": 5}


def spell_data(**spec_overrides: Any) -> dict[str, Any]:
    """A valid spell as plain data. Keyword arguments replace spec keys."""
    spec: dict[str, Any] = {
        "role": "Tend the test field.",
        "model": {"provider": "fake"},
        "fields": [{"name": "test-field", "kind": "service"}],
        "implements": ["noop.read", "noop.write"],
        "maat": [UNHEALTHY],
    }
    spec.update(spec_overrides)
    return {
        "apiVersion": "ushabti/v1alpha1",
        "kind": "Spell",
        "metadata": {"name": "test-spell"},
        "spec": spec,
    }


def make_spell(**spec_overrides: Any) -> Spell:
    return Spell.model_validate(spell_data(**spec_overrides))


def write_spell(path: Path, **spec_overrides: Any) -> Path:
    path.write_text(yaml.safe_dump(spell_data(**spec_overrides)), encoding="utf-8")
    return path


def write_call(call_id: str = "call-1", **arguments: JsonValue) -> ToolCall:
    return ToolCall(id=call_id, name="noop.write", arguments=arguments or {"action": "restart"})


def proposing(*calls: ToolCall, content: str = "Restarting the service.") -> Completion:
    return Completion(content=content, tool_calls=calls, stop_reason=StopReason.TOOL_CALLS)


class Clock:
    """A clock that only moves when told to."""

    def __init__(self, now: datetime = AT) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def sequential_ids() -> Callable[[], str]:
    numbers = count(1)
    return lambda: f"id-{next(numbers)}"


class ScriptedRead(Implement):
    """A read implement that returns the next value of a list on each call."""

    name: ClassVar[str] = "noop.read"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Return scripted observations, one per call."

    def __init__(self, *observations: JsonValue) -> None:
        self.observations = list(observations)

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        if len(self.observations) > 1:
            return self.observations.pop(0)
        return self.observations[0]


class World:
    """An ushabti with everything around it in reach of a test."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        spell: Spell | None = None,
        provider: ModelProvider | None = None,
        read: Implement | None = None,
        weighing: Weighing | None = None,
        clock: Clock | None = None,
        name: str | None = None,
    ) -> None:
        self.path = tmp_path / "papyrus.jsonl"
        self.papyrus = JsonlPapyrus(self.path)
        self.write = NoopWrite()
        self.clock = clock or Clock()
        self.spell = spell or make_spell()
        self.ushabti = Ushabti(
            self.spell,
            papyrus=self.papyrus,
            provider=provider,
            registry=ImplementRegistry([read or NoopRead(), self.write]),
            weighing=weighing,
            name=name,
            clock=self.clock,
            ids=sequential_ids(),
        )

    def entries(self) -> list[PapyrusEntry]:
        return list(self.papyrus.entries())

    def events(self) -> list[str]:
        return [entry.event.value for entry in self.entries()]

    def of(self, event: str) -> list[PapyrusEntry]:
        return [entry for entry in self.entries() if entry.event.value == event]
