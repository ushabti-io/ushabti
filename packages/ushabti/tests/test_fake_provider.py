"""FakeProvider round-trips scripted completions and tool calls."""

from pathlib import Path

import pytest

from ushabti import (
    Completion,
    ImplementKind,
    ImplementSpec,
    Message,
    ModelError,
    ModelProvider,
    Role,
    StopReason,
    ToolCall,
    Usage,
)
from ushabti.models import from_wire_name, to_wire_name
from ushabti.models.fake import FakeProvider

RESTART = ToolCall(
    id="call-1",
    name="kubernetes.restart_deployment",
    arguments={"namespace": "default", "name": "web"},
)
SPEC = ImplementSpec(
    name="noop.write",
    kind=ImplementKind.WRITE,
    description="Pretend.",
    parameters={"type": "object"},
)


async def ask(provider: ModelProvider, text: str = "What now?") -> Completion:
    return await provider.complete(
        system="You tend the test field.", messages=[Message.user(text)], implements=[SPEC]
    )


async def test_scripted_completions_come_back_in_order() -> None:
    first = Completion(
        content="Restarting.", tool_calls=(RESTART,), stop_reason=StopReason.TOOL_CALLS
    )
    second = Completion(content="Done.")
    provider = FakeProvider([first, second])

    assert provider.remaining == 2
    assert await ask(provider) == first
    assert await ask(provider) == second
    assert provider.remaining == 0


async def test_tool_calls_round_trip_exactly() -> None:
    provider = FakeProvider([Completion(tool_calls=(RESTART,))])
    completion = await ask(provider)
    assert completion.tool_calls == (RESTART,)
    assert completion.tool_calls[0].arguments == {"namespace": "default", "name": "web"}


async def test_every_request_is_recorded() -> None:
    provider = FakeProvider()
    await ask(provider, "first")
    await ask(provider, "second")
    assert [request.messages[0].content for request in provider.requests] == ["first", "second"]
    assert provider.requests[0].system == "You tend the test field."
    assert provider.requests[0].implements == (SPEC,)


async def test_an_exhausted_script_proposes_nothing() -> None:
    completion = await ask(FakeProvider())
    assert completion.tool_calls == ()
    assert completion.content == "Nothing to do."
    assert completion.stop_reason is StopReason.END


async def test_the_exhausted_reply_can_be_replaced() -> None:
    provider = FakeProvider(when_exhausted=Completion(content="Still here."))
    assert (await ask(provider)).content == "Still here."
    assert (await ask(provider)).content == "Still here."


async def test_the_same_script_always_gives_the_same_answers() -> None:
    script = [Completion(content="a", tool_calls=(RESTART,)), Completion(content="b")]
    runs = []
    for _ in range(2):
        provider = FakeProvider(script)
        runs.append([await ask(provider), await ask(provider), await ask(provider)])
    assert runs[0] == runs[1]


def test_completions_and_messages_round_trip_through_json() -> None:
    completion = Completion(
        content="Restarting.",
        tool_calls=(RESTART,),
        stop_reason=StopReason.TOOL_CALLS,
        usage=Usage(input_tokens=12, output_tokens=7),
        raw={"provider": "fake", "content": [{"type": "text", "text": "Restarting."}]},
    )
    assert Completion.model_validate_json(completion.model_dump_json()) == completion

    message = completion.as_message()
    assert message.role is Role.ASSISTANT
    assert message.content == "Restarting."
    assert message.tool_calls == (RESTART,)
    assert message.raw == completion.raw
    assert Message.model_validate_json(message.model_dump_json()) == message

    result = Message.tool_result(RESTART, "dry run: recorded, not run", is_error=True)
    assert result.role is Role.TOOL
    assert result.tool_call_id == "call-1"
    assert result.is_error
    assert Message.model_validate_json(result.model_dump_json()) == result


async def test_a_script_loads_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "script.yaml"
    path.write_text(
        """
- content: Restarting the web deployment.
  tool_calls:
    - name: kubernetes.restart_deployment
      arguments: {namespace: default, name: web}
    - name: noop.read
- content: Done.
"""
    )
    provider = FakeProvider.from_script_file(path)

    first = await ask(provider)
    assert first.content == "Restarting the web deployment."
    assert first.stop_reason is StopReason.TOOL_CALLS
    assert [(call.id, call.name) for call in first.tool_calls] == [
        ("call-1", "kubernetes.restart_deployment"),
        ("call-2", "noop.read"),
    ]
    assert first.tool_calls[0].arguments == {"namespace": "default", "name": "web"}
    assert first.tool_calls[1].arguments == {}

    second = await ask(provider)
    assert second.content == "Done."
    assert second.stop_reason is StopReason.END


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("content: not a list", "must be a list of completions"),
        ("- just a string", "holds an item that is not a map"),
        ("- [unclosed", "cannot read the fake model script"),
    ],
)
def test_a_bad_script_is_reported(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "script.yaml"
    path.write_text(text)
    with pytest.raises(ModelError, match=message):
        FakeProvider.from_script_file(path)


def test_a_missing_script_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ModelError, match="cannot read the fake model script"):
        FakeProvider.from_script_file(tmp_path / "absent.yaml")


@pytest.mark.parametrize("name", ["kubernetes.list_pods", "noop.read", "prometheus.query_range"])
def test_implement_names_survive_the_trip_to_a_model_api(name: str) -> None:
    wire = to_wire_name(name)
    assert "." not in wire
    assert from_wire_name(wire) == name
