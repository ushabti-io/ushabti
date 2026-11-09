"""Anthropic model provider. Needs the ``anthropic`` extra.

The SDK is imported when a provider is built, never when this module is
imported, so the core works without the extra.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Final, cast

from pydantic import JsonValue, SecretStr

from ushabti.errors import MissingExtraError, ModelError
from ushabti.implements import ImplementSpec
from ushabti.models import (
    Completion,
    Message,
    Role,
    StopReason,
    ToolCall,
    Usage,
    from_wire_name,
    to_wire_name,
)

if TYPE_CHECKING:
    from anthropic import AsyncAnthropic
    from anthropic.types import Message as AnthropicMessage
    from anthropic.types import MessageParam, ToolParam, ToolResultBlockParam

PROVIDER: Final = "anthropic"

DEFAULT_MODEL: Final = "claude-opus-5-5"
"""Used when a spell names no model. A spell or USHABTI_MODEL overrides it."""

DEFAULT_MAX_TOKENS: Final = 16000

_STOP_REASONS: Final = {
    "end_turn": StopReason.END,
    "stop_sequence": StopReason.END,
    "tool_use": StopReason.TOOL_CALLS,
    "max_tokens": StopReason.MAX_TOKENS,
    "model_context_window_exceeded": StopReason.MAX_TOKENS,
    "refusal": StopReason.REFUSAL,
}


class AnthropicProvider:
    """Non-streaming completions with tool calls through the Anthropic SDK."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        client: AsyncAnthropic | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            try:
                import anthropic
            except ImportError as error:
                raise MissingExtraError("anthropic", "anthropic") from error
            try:
                # With no key given, the SDK resolves credentials from the environment.
                client = anthropic.AsyncAnthropic(
                    api_key=None if api_key is None else api_key.get_secret_value(),
                    base_url=base_url,
                )
            except anthropic.AnthropicError as error:
                raise ModelError(f"cannot set up the Anthropic client: {error}") from error
        self._client = client

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        implements: Sequence[ImplementSpec],
    ) -> Completion:
        import anthropic

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system or anthropic.omit,
                messages=_to_messages(messages),
                tools=[_to_tool(spec) for spec in implements] if implements else anthropic.omit,
            )
        except anthropic.APIStatusError as error:
            raise ModelError(
                f"the Anthropic API returned {error.status_code}: {error.message}"
            ) from error
        except anthropic.APIConnectionError as error:
            raise ModelError(f"could not reach the Anthropic API: {error}") from error
        return _to_completion(response)


def _to_tool(spec: ImplementSpec) -> ToolParam:
    schema: dict[str, object] = dict(spec.parameters)
    return {
        "name": to_wire_name(spec.name),
        "description": spec.description,
        "input_schema": schema,
    }


def _to_messages(messages: Sequence[Message]) -> list[MessageParam]:
    converted: list[MessageParam] = []
    results: list[ToolResultBlockParam] = []

    def flush() -> None:
        # Every result of one assistant turn goes back in a single user message.
        if results:
            converted.append({"role": "user", "content": list(results)})
            results.clear()

    for message in messages:
        if message.role is Role.TOOL:
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id or "",
                    "content": message.content,
                    "is_error": message.is_error,
                }
            )
            continue
        flush()
        if message.role is Role.USER:
            converted.append({"role": "user", "content": message.content})
        else:
            converted.append({"role": "assistant", "content": _assistant_content(message)})
    flush()
    return converted


def _assistant_content(message: Message) -> Any:
    raw = message.raw
    if raw is not None and raw.get("provider") == PROVIDER:
        # Replay the turn exactly as it came back, thinking blocks included.
        return raw["content"]
    blocks: list[dict[str, JsonValue]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    blocks.extend(
        {
            "type": "tool_use",
            "id": call.id,
            "name": to_wire_name(call.name),
            "input": call.arguments,
        }
        for call in message.tool_calls
    )
    return blocks


def _to_completion(response: AnthropicMessage) -> Completion:
    text: list[str] = []
    calls: list[ToolCall] = []
    for block in response.content:
        if block.type == "text":
            text.append(block.text)
        elif block.type == "tool_use":
            calls.append(
                ToolCall(
                    id=block.id,
                    name=from_wire_name(block.name),
                    arguments=cast("dict[str, JsonValue]", block.input),
                )
            )
    content = cast("list[JsonValue]", [block.to_dict(mode="json") for block in response.content])
    return Completion(
        content="".join(text),
        tool_calls=tuple(calls),
        stop_reason=_STOP_REASONS.get(response.stop_reason or "", StopReason.OTHER),
        usage=Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        ),
        raw={"provider": PROVIDER, "content": content},
    )
