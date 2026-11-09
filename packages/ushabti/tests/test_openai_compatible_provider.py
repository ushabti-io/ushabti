"""OpenAICompatibleProvider: request and response conversion against the real SDK types."""

from typing import Any, cast

import pytest
from pydantic import SecretStr

from ushabti import (
    Completion,
    ImplementKind,
    ImplementSpec,
    Message,
    ModelError,
    StopReason,
    ToolCall,
)
from ushabti.models.openai_compatible import OpenAICompatibleProvider

openai = pytest.importorskip("openai")
httpx2 = pytest.importorskip("httpx2")

LIST_PODS = ImplementSpec(
    name="kubernetes.list_pods",
    kind=ImplementKind.READ,
    description="List pods.",
    parameters={"type": "object", "properties": {"namespace": {"type": "string"}}},
)


def response(
    *,
    content: str | None = "Checking the pods.",
    tool_calls: list[dict[str, Any]] | None = None,
    finish_reason: str = "tool_calls",
    usage: dict[str, int] | None = None,
    choices: bool = True,
) -> Any:
    message = {"role": "assistant", "content": content, "tool_calls": tool_calls}
    return openai.types.chat.ChatCompletion.model_validate(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}]
            if choices
            else [],
            "usage": usage,
        }
    )


def function_call(arguments: str, call_id: str = "call_1") -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "kubernetes-list_pods", "arguments": arguments},
    }


class StubCompletions:
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
        completions = StubCompletions(outcome)
        self.completions = completions
        self.chat = type("Chat", (), {"completions": completions})()


def provider_with(outcome: Any, **kwargs: Any) -> tuple[OpenAICompatibleProvider, StubCompletions]:
    client = StubClient(outcome)
    provider = OpenAICompatibleProvider(model="test-model", client=cast("Any", client), **kwargs)
    return provider, client.completions


async def complete(
    provider: OpenAICompatibleProvider, *messages: Message, **kwargs: Any
) -> Completion:
    return await provider.complete(
        system=kwargs.get("system", "You tend the cluster."),
        messages=list(messages) or [Message.user("Is the cluster healthy?")],
        implements=kwargs.get("implements", [LIST_PODS]),
    )


async def test_a_reply_with_a_tool_call_becomes_a_completion() -> None:
    provider, _ = provider_with(
        response(
            tool_calls=[function_call('{"namespace": "default"}')],
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )
    )
    completion = await complete(provider)

    assert completion.content == "Checking the pods."
    assert completion.tool_calls == (
        ToolCall(id="call_1", name="kubernetes.list_pods", arguments={"namespace": "default"}),
    )
    assert completion.stop_reason is StopReason.TOOL_CALLS
    assert completion.usage.input_tokens == 11
    assert completion.usage.output_tokens == 7


async def test_the_request_follows_the_chat_completions_api() -> None:
    provider, completions = provider_with(response(finish_reason="stop"), max_tokens=512)
    await complete(provider)

    assert completions.requests == [
        {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "You tend the cluster."},
                {"role": "user", "content": "Is the cluster healthy?"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "kubernetes-list_pods",
                        "description": "List pods.",
                        "parameters": {
                            "type": "object",
                            "properties": {"namespace": {"type": "string"}},
                        },
                    },
                }
            ],
            "max_tokens": 512,
        }
    ]


async def test_optional_parts_are_left_out() -> None:
    provider, completions = provider_with(response(content=None, finish_reason="stop"))
    completion = await complete(provider, system="", implements=[])

    request = completions.requests[0]
    assert request["messages"] == [{"role": "user", "content": "Is the cluster healthy?"}]
    assert request["tools"] is openai.omit
    assert request["max_tokens"] is openai.omit
    assert completion.content == ""
    assert completion.usage.input_tokens == 0
    assert completion.stop_reason is StopReason.END


async def test_a_conversation_with_tool_results_is_sent_back() -> None:
    provider, completions = provider_with(response(finish_reason="stop"))
    first = Completion(
        content="",
        tool_calls=(ToolCall(id="call_1", name="kubernetes.list_pods", arguments={"n": 1}),),
    )
    await complete(
        provider,
        Message.user("Is the cluster healthy?"),
        first.as_message(),
        Message.tool_result(first.tool_calls[0], '{"pods": 3}'),
        Completion(content="All good.").as_message(),
    )

    assert completions.requests[0]["messages"][2:] == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "kubernetes-list_pods", "arguments": '{"n": 1}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"pods": 3}'},
        {"role": "assistant", "content": "All good."},
    ]


@pytest.mark.parametrize(
    ("finish_reason", "expected"),
    [
        ("stop", StopReason.END),
        ("tool_calls", StopReason.TOOL_CALLS),
        ("function_call", StopReason.TOOL_CALLS),
        ("length", StopReason.MAX_TOKENS),
        ("content_filter", StopReason.REFUSAL),
    ],
)
async def test_finish_reasons_are_mapped(finish_reason: str, expected: StopReason) -> None:
    provider, _ = provider_with(response(finish_reason=finish_reason))
    assert (await complete(provider)).stop_reason is expected


async def test_empty_arguments_mean_no_arguments() -> None:
    provider, _ = provider_with(response(tool_calls=[function_call("")]))
    assert (await complete(provider)).tool_calls[0].arguments == {}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [("{not json", "not valid JSON"), ("[1, 2]", "not a JSON object")],
)
async def test_malformed_arguments_are_an_error(arguments: str, message: str) -> None:
    provider, _ = provider_with(response(tool_calls=[function_call(arguments)]))
    with pytest.raises(ModelError, match=message):
        await complete(provider)


async def test_custom_tool_calls_are_skipped() -> None:
    custom = {"id": "call_9", "type": "custom", "custom": {"name": "other", "input": "x"}}
    provider, _ = provider_with(response(tool_calls=[custom, function_call("{}")]))
    assert [call.id for call in (await complete(provider)).tool_calls] == ["call_1"]


async def test_no_choices_is_an_error() -> None:
    provider, _ = provider_with(response(choices=False))
    with pytest.raises(ModelError, match="returned no choices"):
        await complete(provider)


async def test_api_errors_become_model_errors() -> None:
    request = httpx2.Request("POST", "http://model.example.invalid/v1/chat/completions")
    status = openai.InternalServerError(
        "the server is unwell", response=httpx2.Response(500, request=request), body=None
    )
    provider, _ = provider_with(status)
    with pytest.raises(ModelError, match="the model endpoint returned 500: the server is unwell"):
        await complete(provider)

    provider, _ = provider_with(openai.APIConnectionError(request=request))
    with pytest.raises(ModelError, match="could not reach the model endpoint"):
        await complete(provider)


def test_a_local_server_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        model="test-model", base_url="http://model.example.invalid:11434/v1"
    )
    assert provider._client.api_key == "unused"
    assert str(provider._client.base_url).startswith("http://model.example.invalid:11434/v1")


def test_a_given_key_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://model.example.invalid:11434/v1",
    )
    assert provider._client.api_key == "test-key"


def test_the_hosted_api_without_a_key_is_a_model_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_ADMIN_KEY", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ModelError, match="cannot set up the OpenAI client"):
        OpenAICompatibleProvider(model="test-model")
