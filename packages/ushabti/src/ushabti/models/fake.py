"""A model provider that replays a script. Deterministic, and needs no network."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from ushabti.errors import ModelError
from ushabti.implements import ImplementSpec
from ushabti.models import Completion, Message, StopReason, ToolCall


@dataclass(frozen=True, slots=True)
class FakeRequest:
    """One call the fake provider received, kept so a test can inspect it."""

    system: str
    messages: tuple[Message, ...]
    implements: tuple[ImplementSpec, ...]


class FakeProvider:
    """Returns scripted completions in order.

    When the script runs out it returns ``when_exhausted``, which by default
    proposes nothing. ``requests`` records every call received.
    """

    def __init__(
        self,
        script: Iterable[Completion] = (),
        *,
        when_exhausted: Completion | None = None,
    ) -> None:
        self._script = list(script)
        self._when_exhausted = when_exhausted or Completion(content="Nothing to do.")
        self.requests: list[FakeRequest] = []

    @classmethod
    def from_script_file(cls, path: Path | str) -> "FakeProvider":
        """Load a script from YAML: a list of completions.

        Each item may hold ``content`` and ``tool_calls``. A tool call needs a
        ``name`` and may give ``arguments`` and an ``id``.
        """
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as error:
            raise ModelError(f"cannot read the fake model script {path}: {error}") from error
        if not isinstance(data, list):
            raise ModelError(f"the fake model script {path} must be a list of completions")
        script: list[Completion] = []
        counter = 0
        for item in data:
            if not isinstance(item, dict):
                raise ModelError(f"the fake model script {path} holds an item that is not a map")
            calls: list[ToolCall] = []
            for call in item.get("tool_calls") or ():
                counter += 1
                calls.append(ToolCall.model_validate({"id": f"call-{counter}", **call}))
            script.append(
                Completion(
                    content=str(item.get("content") or ""),
                    tool_calls=tuple(calls),
                    stop_reason=StopReason.TOOL_CALLS if calls else StopReason.END,
                )
            )
        return cls(script)

    @property
    def remaining(self) -> int:
        """How many scripted completions have not been returned yet."""
        return len(self._script)

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        implements: Sequence[ImplementSpec],
    ) -> Completion:
        self.requests.append(FakeRequest(system, tuple(messages), tuple(implements)))
        if self._script:
            return self._script.pop(0)
        return self._when_exhausted
