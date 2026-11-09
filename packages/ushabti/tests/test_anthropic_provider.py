"""AnthropicProvider: request and response conversion, checked against the real SDK types."""

from typing import Any, cast

import pytest

from ushabti import (
    Completion,
    ImplementKind,
    ImplementSpec,
    Message,
    ModelError,
    StopReason,
    ToolCall,
)
from ushabti.models.anthropic import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, AnthropicProvider

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

LIST_PODS = ImplementSpec(
    name="kubernetes.list_pods",
    kind=ImplementKind.READ,
    description="List pods.",
    parameters={"type": "object", "properties": {"namespace": {"type": "string"}}},
)

TOOL_USE_BLOCKS: list[dict[str, Any]] = [
    {"type": "thinking", "thinking": "", "signature": "opaque-signature"},
    {"type": "text", "text": "Checking the pods."},
    {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "kubernetes-list_pods",
        "input": {"namespace": "default"},
    },
]


def response(blocks: list[dict[str, Any]], stop_reason: str = "tool_use") -> Any:
    return anthropic.types.Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "test-model",
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 11, "output_tokens": 7},
            "content": blocks,
        }
    )


class StubMessages:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class StubClient:
    def __init__(self, outcome: Any) -> None:
        self.messages = StubMessages(outcome)


def provider_with(outcome: Any, **kwargs: Any) -> tuple[AnthropicProvider, StubMessages]:
    client = StubClient(outcome)
    provider = AnthropicProvider(model="test-model", client=cast("Any", client), **kwargs)
    return provider, client.messages


async def complete(provider: AnthropicProvider, *messages: Message, **kwargs: Any) -> Completion:
    return await provider.complete(
        system=kwargs.get("system", "You tend the cluster."),
        messages=list(messages) or [Message.user("Is the cluster healthy?")],
        implements=kwargs.get("implements", [LIST_PODS]),
    )


async def test_a_reply_with_a_tool_call_becomes_a_completion() -> None:
    provider, _ = provider_with(response(TOOL_USE_BLOCKS))
    completion = await complete(provider)

    assert completion.content == "Checking the pods."
    assert completion.tool_calls == (
        ToolCall(id="toolu_1", name="kubernetes.list_pods", arguments={"namespace": "default"}),
    )
    assert completion.stop_reason is StopReason.TOOL_CALLS
    assert completion.usage.input_tokens == 11
    assert completion.usage.output_tokens == 7
    assert completion.raw == {"provider": "anthropic", "content": TOOL_USE_BLOCKS}


async def test_the_request_follows_the_messages_api() -> None:
    provider, messages = provider_with(response(TOOL_USE_BLOCKS), max_tokens=2048)
    await complete(provider)

    assert messages.requests == [
        {
            "model": "test-model",
            "max_tokens": 2048,
            "system": "You tend the cluster.",
            "messages": [{"role": "user", "content": "Is the cluster healthy?"}],
            "tools": [
                {
                    "name": "kubernetes-list_pods",
                    "description": "List pods.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"namespace": {"type": "string"}},
                    },
                }
            ],
        }
    ]


async def test_an_empty_system_prompt_and_no_implements_are_left_out() -> None:
    provider, messages = provider_with(response([{"type": "text", "text": "Fine."}], "end_turn"))
    completion = await complete(provider, system="", implements=[])

    assert messages.requests[0]["system"] is anthropic.omit
    assert messages.requests[0]["tools"] is anthropic.omit
    assert completion.stop_reason is StopReason.END
    assert completion.tool_calls == ()


async def test_its_own_assistant_turn_is_replayed_unchanged_with_results_in_one_message() -> None:
    provider, messages = provider_with(
        response([{"type": "text", "text": "All good."}], "end_turn")
    )
    first = Completion(
        content="Checking.",
        tool_calls=(
            ToolCall(id="toolu_1", name="kubernetes.list_pods", arguments={"namespace": "a"}),
            ToolCall(id="toolu_2", name="kubernetes.list_pods", arguments={"namespace": "b"}),
        ),
        raw={"provider": "anthropic", "content": TOOL_USE_BLOCKS},
    )
    await complete(
        provider,
        Message.user("Is the cluster healthy?"),
        first.as_message(),
        Message.tool_result(first.tool_calls[0], '{"pods": 3}'),
        Message.tool_result(first.tool_calls[1], "rejected: not listed", is_error=True),
        Message.user("And now?"),
    )

    assert messages.requests[0]["messages"] == [
        {"role": "user", "content": "Is the cluster healthy?"},
        # Thinking blocks and all, exactly as the API returned them.
        {"role": "assistant", "content": TOOL_USE_BLOCKS},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": '{"pods": 3}',
                    "is_error": False,
                },
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_2",
                    "content": "rejected: not listed",
                    "is_error": True,
                },
            ],
        },
        {"role": "user", "content": "And now?"},
    ]


async def test_an_assistant_turn_from_elsewhere_is_rebuilt_from_its_parts() -> None:
    provider, messages = provider_with(response([{"type": "text", "text": "ok"}], "end_turn"))
    foreign = Completion(
        content="Looking.",
        tool_calls=(ToolCall(id="call-1", name="noop.read", arguments={"a": 1}),),
        raw={"provider": "somewhere-else", "content": "ignored"},
    )
    await complete(
        provider,
        Message.user("hi"),
        foreign.as_message(),
        Message.tool_result(foreign.tool_calls[0], "{}"),
    )

    assert messages.requests[0]["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Looking."},
            {"type": "tool_use", "id": "call-1", "name": "noop-read", "input": {"a": 1}},
        ],
    }
    assert messages.requests[0]["messages"][2]["role"] == "user"


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", StopReason.END),
        ("stop_sequence", StopReason.END),
        ("tool_use", StopReason.TOOL_CALLS),
        ("max_tokens", StopReason.MAX_TOKENS),
        ("model_context_window_exceeded", StopReason.MAX_TOKENS),
        ("refusal", StopReason.REFUSAL),
        ("pause_turn", StopReason.OTHER),
    ],
)
async def test_stop_reasons_are_mapped(stop_reason: str, expected: StopReason) -> None:
    provider, _ = provider_with(response([{"type": "text", "text": "x"}], stop_reason))
    assert (await complete(provider)).stop_reason is expected


async def test_api_errors_become_model_errors() -> None:
    request = httpx2.Request("POST", "https://api.example.invalid/v1/messages")
    status = anthropic.InternalServerError(
        "the service is unwell", response=httpx2.Response(500, request=request), body=None
    )
    provider, _ = provider_with(status)
    with pytest.raises(ModelError, match="the Anthropic API returned 500: the service is unwell"):
        await complete(provider)

    provider, _ = provider_with(anthropic.APIConnectionError(request=request))
    with pytest.raises(ModelError, match="could not reach the Anthropic API"):
        await complete(provider)


def test_defaults() -> None:
    provider = AnthropicProvider(client=cast("Any", StubClient(None)))
    assert provider.model == DEFAULT_MODEL
    assert provider.max_tokens == DEFAULT_MAX_TOKENS
