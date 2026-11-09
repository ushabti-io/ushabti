"""Model providers: the adapters that let any model answer a summons.

The provider modules import their SDKs only when a provider is built, so the
core installs and imports without any of the optional extras.
"""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import pydantic
from pydantic import JsonValue

from ushabti._base import WireModel
from ushabti.errors import ModelError
from ushabti.implements import ImplementSpec
from ushabti.spell import ModelProviderName, ModelSpec, resolve_optional, resolve_secret


class Role(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class StopReason(StrEnum):
    """Why a model stopped, in terms every provider can be mapped onto."""

    END = "end"
    TOOL_CALLS = "tool_calls"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    OTHER = "other"


class ToolCall(WireModel):
    """A model's request to use one implement."""

    id: str
    """Identifier the provider gave the call. The result must quote it."""

    name: str
    """Name of the implement, such as `kubernetes.list_pods`."""

    arguments: dict[str, JsonValue] = pydantic.Field(default_factory=dict)
    """Arguments the model proposed."""


class Message(WireModel):
    """One turn of the conversation with a model."""

    role: Role
    content: str = ""

    tool_calls: tuple[ToolCall, ...] = ()
    """Implement calls the model proposed. Assistant turns only."""

    tool_call_id: str | None = None
    """Which call this is the result of. Tool turns only."""

    is_error: bool = False
    """Whether the result reports a failure or a refusal. Tool turns only."""

    raw: dict[str, JsonValue] | None = None
    """The assistant turn exactly as its provider returned it. A provider replays
    this unchanged, because some models reject a history that was edited."""

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role=Role.USER, content=content)

    @classmethod
    def tool_result(cls, call: ToolCall, content: str, *, is_error: bool = False) -> "Message":
        return cls(role=Role.TOOL, content=content, tool_call_id=call.id, is_error=is_error)


class Usage(WireModel):
    input_tokens: int = 0
    output_tokens: int = 0


class Completion(WireModel):
    """One reply from a model."""

    content: str = ""
    """Text of the reply."""

    tool_calls: tuple[ToolCall, ...] = ()
    """Implement calls the model proposed."""

    stop_reason: StopReason = StopReason.END
    """Why the model stopped."""

    usage: Usage = Usage()
    """Tokens the request used."""

    raw: dict[str, JsonValue] | None = None
    """The reply exactly as the provider returned it, for replay."""

    def as_message(self) -> Message:
        """This reply as the assistant turn to append to the conversation."""
        return Message(
            role=Role.ASSISTANT,
            content=self.content,
            tool_calls=self.tool_calls,
            raw=self.raw,
        )


class ModelProvider(Protocol):
    """Anything that can turn a conversation into a completion."""

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        implements: Sequence[ImplementSpec],
    ) -> Completion: ...


def to_wire_name(name: str) -> str:
    """An implement name in the form model APIs accept.

    Tool names may hold letters, digits, underscores, and hyphens, but no
    dots. Implement names never hold a hyphen, so the swap is reversible.
    """
    return name.replace(".", "-")


def from_wire_name(name: str) -> str:
    """Undo ``to_wire_name``."""
    return name.replace("-", ".")


def build_provider(
    spec: ModelSpec,
    *,
    env: Mapping[str, str] | None = None,
    fake_script: Path | None = None,
) -> ModelProvider:
    """Build the provider a spell asks for, resolving its environment references."""
    api_key = resolve_secret(spec.api_key, env)
    base_url = resolve_optional(spec.base_url, env)
    name = resolve_optional(spec.name, env)

    if spec.provider is ModelProviderName.FAKE:
        from ushabti.models.fake import FakeProvider

        return FakeProvider() if fake_script is None else FakeProvider.from_script_file(fake_script)

    if spec.provider is ModelProviderName.ANTHROPIC:
        from ushabti.models.anthropic import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, AnthropicProvider

        return AnthropicProvider(
            model=name or DEFAULT_MODEL,
            api_key=api_key,
            base_url=base_url,
            max_tokens=spec.max_tokens or DEFAULT_MAX_TOKENS,
        )

    from ushabti.models.openai_compatible import OpenAICompatibleProvider

    if name is None:
        raise ModelError(
            f"spec.model.name is {spec.name}, which is not set; "
            "an openai_compatible provider needs a model name"
        )
    return OpenAICompatibleProvider(
        model=name,
        api_key=api_key,
        base_url=base_url,
        max_tokens=spec.max_tokens,
    )
