"""Provider for any OpenAI-compatible endpoint. Needs the ``openai`` extra.

It speaks the Chat Completions API, which is the one local servers such as
Ollama and vLLM implement. Point it at them with ``base_url``.

The SDK is imported when a provider is built, never when this module is
imported, so the core works without the extra.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final

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
    from openai import AsyncOpenAI
    from openai.types.chat import (
        ChatCompletion,
        ChatCompletionFunctionToolParam,
        ChatCompletionMessageParam,
    )

_UNUSED_KEY: Final = "unused"
"""Sent to local servers that ignore the key. The SDK refuses to start without one."""

_STOP_REASONS: Final = {
    "stop": StopReason.END,
    "tool_calls": StopReason.TOOL_CALLS,
    "function_call": StopReason.TOOL_CALLS,
    "length": StopReason.MAX_TOKENS,
    "content_filter": StopReason.REFUSAL,
}


class OpenAICompatibleProvider:
    """Non-streaming chat completions with tool calls through the OpenAI SDK."""

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
        max_tokens: int | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.max_tokens = max_tokens
        if client is None:
            try:
                import openai
            except ImportError as error:
                raise MissingExtraError("openai", "openai") from error
            key = None if api_key is None else api_key.get_secret_value()
            if key is None and base_url is not None and not os.environ.get("OPENAI_API_KEY"):
                key = _UNUSED_KEY
            try:
                client = openai.AsyncOpenAI(api_key=key, base_url=base_url)
            except openai.OpenAIError as error:
                raise ModelError(f"cannot set up the OpenAI client: {error}") from error
        self._client = client

    async def complete(
        self,
        *,
        system: str,
        messages: Sequence[Message],
        implements: Sequence[ImplementSpec],
    ) -> Completion:
        import openai

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=_to_messages(system, messages),
                tools=[_to_tool(spec) for spec in implements] if implements else openai.omit,
                # max_tokens is the spelling local servers understand.
                max_tokens=self.max_tokens if self.max_tokens is not None else openai.omit,
            )
        except openai.APIStatusError as error:
            raise ModelError(
                f"the model endpoint returned {error.status_code}: {error.message}"
            ) from error
        except openai.APIConnectionError as error:
            raise ModelError(f"could not reach the model endpoint: {error}") from error
        return _to_completion(response)


def _to_tool(spec: ImplementSpec) -> ChatCompletionFunctionToolParam:
    parameters: dict[str, object] = dict(spec.parameters)
    return {
        "type": "function",
        "function": {
            "name": to_wire_name(spec.name),
            "description": spec.description,
            "parameters": parameters,
        },
    }


def _to_messages(system: str, messages: Sequence[Message]) -> list[ChatCompletionMessageParam]:
    converted: list[ChatCompletionMessageParam] = []
    if system:
        converted.append({"role": "system", "content": system})
    for message in messages:
        if message.role is Role.USER:
            converted.append({"role": "user", "content": message.content})
        elif message.role is Role.TOOL:
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id or "",
                    "content": message.content,
                }
            )
        elif message.tool_calls:
            converted.append(
                {
                    "role": "assistant",
                    "content": message.content or None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": to_wire_name(call.name),
                                "arguments": json.dumps(call.arguments),
                            },
                        }
                        for call in message.tool_calls
                    ],
                }
            )
        else:
            converted.append({"role": "assistant", "content": message.content})
    return converted


def _to_completion(response: ChatCompletion) -> Completion:
    if not response.choices:
        raise ModelError("the model endpoint returned no choices")
    choice = response.choices[0]
    calls: list[ToolCall] = []
    for call in choice.message.tool_calls or ():
        if call.type != "function":
            continue
        calls.append(
            ToolCall(
                id=call.id,
                name=from_wire_name(call.function.name),
                arguments=_parse_arguments(call.function.name, call.function.arguments),
            )
        )
    usage = response.usage
    return Completion(
        content=choice.message.content or "",
        tool_calls=tuple(calls),
        stop_reason=_STOP_REASONS.get(choice.finish_reason, StopReason.OTHER),
        usage=Usage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        ),
    )


def _parse_arguments(name: str, arguments: str) -> dict[str, JsonValue]:
    try:
        parsed: JsonValue = json.loads(arguments or "{}")
    except json.JSONDecodeError as error:
        raise ModelError(f"the model sent arguments for {name} that are not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ModelError(f"the model sent arguments for {name} that are not a JSON object")
    return parsed
