"""The vigil loop, summons handling, the weighing in practice, and halt."""

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path

from agent_testkit import (
    AT,
    LOOSE,
    UNHEALTHY,
    Clock,
    ScriptedRead,
    World,
    make_spell,
    proposing,
    write_call,
)
from ushabti import (
    Completion,
    DefaultWeighing,
    ImplementSpec,
    Message,
    ModelError,
    PapyrusEntry,
    PapyrusEvent,
    StopReason,
    Summons,
    SummonsSource,
    SummonsState,
    ToolCall,
    Verdict,
    WeighingRequest,
    WeighingResult,
)
from ushabti.models.fake import FakeProvider
from ushabti_agent.runtime import Ushabti

DONE = Completion(content="Recorded. Nothing else to do.")


def operator_summons(reason: str = "Restart the service.") -> Summons:
    return Summons(id="op-1", source=SummonsSource.OPERATOR, reason=reason, created_at=AT)


# One tick, end to end


async def test_one_tick_end_to_end_writes_exactly_these_papyrus_entries(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider)

    report = await world.ushabti.once()

    isfet = {
        "id": "id-1",
        "assertion": "service-healthy",
        "field": "test-field",
        "description": "The service reports healthy.",
        "observed": False,
        "expected": "healthy eq true",
        "detected_at": "2026-01-02T03:04:05Z",
    }
    summons = {
        "id": "id-2",
        "source": "vigil",
        "reason": (
            "Ma'at assertion service-healthy does not hold. The service reports healthy. "
            "Expected healthy eq true; observed false."
        ),
        "field": "test-field",
        "implement": None,
        "cause": "id-1",
        "state": "pending",
        "detail": None,
        "created_at": "2026-01-02T03:04:05Z",
    }

    def entry(
        event: PapyrusEvent, payload: Mapping[str, object], verdict: Verdict | None = None
    ) -> PapyrusEntry:
        return PapyrusEntry.model_validate(
            {
                "timestamp": AT,
                "ushabti": "test-spell",
                "event": event,
                "payload": payload,
                "verdict": verdict,
            }
        )

    assert world.entries() == [
        entry(PapyrusEvent.VIGIL_TICK, {"tick": 1, "halted": False}),
        entry(
            PapyrusEvent.OBSERVATION,
            {
                "assertion": "service-healthy",
                "implement": "noop.read",
                "field": "test-field",
                "ok": True,
                "data": {"healthy": False},
            },
        ),
        entry(PapyrusEvent.ISFET, isfet),
        entry(PapyrusEvent.SUMMONS, summons),
        entry(
            PapyrusEvent.PROPOSAL,
            {
                "summons": "id-2",
                "step": 1,
                "content": "Restarting the service.",
                "stop_reason": "tool_calls",
                "calls": [
                    {"id": "call-1", "name": "noop.write", "arguments": {"action": "restart"}}
                ],
            },
        ),
        entry(
            PapyrusEvent.VERDICT,
            {
                "summons": "id-2",
                "call": "call-1",
                "implement": "noop.write",
                "kind": "write",
                "arguments": {"action": "restart"},
                "rule": "dry_run",
                "reason": "the spell has dry_run set, so noop.write is recorded and not run",
            },
            Verdict.DRY_RUN,
        ),
        entry(
            PapyrusEvent.PROPOSAL,
            {
                "summons": "id-2",
                "step": 2,
                "content": "Recorded. Nothing else to do.",
                "stop_reason": "end",
                "calls": [],
            },
        ),
        entry(
            PapyrusEvent.SUMMONS,
            {**summons, "state": "answered", "detail": "verdicts: 1 DRY_RUN"},
        ),
    ]

    assert report.tick == 1
    assert [s.id for s in report.raised] == ["id-2"]
    assert [s.state for s in report.answered] == [SummonsState.ANSWERED]
    assert len(report.isfet) == 1
    assert world.write.calls == []


async def test_the_model_sees_the_role_the_summons_and_only_the_listed_implements(
    tmp_path: Path,
) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(
        tmp_path,
        provider=provider,
        spell=make_spell(
            instructions="Prefer a restart over a scale-up.", implements=["noop.read", "noop.write"]
        ),
    )
    await world.ushabti.once()

    first, second = provider.requests
    assert "You are test-spell, an ushabti" in first.system
    assert "Your role: Tend the test field." in first.system
    assert "Prefer a restart over a scale-up." in first.system
    assert "- service-healthy: The service reports healthy." in first.system
    assert "- test-field (service)" in first.system
    assert [spec.name for spec in first.implements] == ["noop.read", "noop.write"]
    assert first.messages[0].content.startswith("You are summoned by the vigil.")
    assert "Field: test-field" in first.messages[0].content

    # The second request carries the assistant turn and the result of the weighing.
    assert [message.role.value for message in second.messages] == ["user", "assistant", "tool"]
    result = second.messages[2]
    assert result.tool_call_id == "call-1"
    assert result.content.startswith("Dry run: the call was recorded and not run.")


# dry_run


async def test_with_dry_run_no_write_implement_executes(tmp_path: Path) -> None:
    calls = [write_call(f"call-{n}", action="restart", attempt=n) for n in range(1, 4)]
    provider = FakeProvider([proposing(*calls[:2]), proposing(calls[2]), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing={"dry_run": True}))

    report = await world.ushabti.once()

    assert world.write.calls == [], "a dry run must never execute a write"
    assert [entry.verdict for entry in world.of("verdict")] == [Verdict.DRY_RUN] * 3
    assert world.of("action") == []
    assert report.answered[0].detail == "verdicts: 3 DRY_RUN"


async def test_dry_run_is_what_a_spell_gets_when_it_says_nothing(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider)
    assert world.spell.spec.weighing.dry_run is True
    await world.ushabti.once()
    assert world.write.calls == []


async def test_reads_still_run_under_dry_run(tmp_path: Path) -> None:
    read = ToolCall(id="call-1", name="noop.read", arguments={"look": "here"})
    provider = FakeProvider([proposing(read), DONE])
    world = World(tmp_path, provider=provider)
    await world.ushabti.once()

    (verdict,) = world.of("verdict")
    (action,) = world.of("action")
    assert verdict.verdict is Verdict.PASS
    assert verdict.payload["rule"] == "read"
    assert action.payload == {
        "summons": "id-2",
        "call": "call-1",
        "implement": "noop.read",
        "kind": "read",
        "ok": True,
        "result": {"look": "here"},
    }
    assert provider.requests[1].messages[2].content == '{"look": "here"}'


# Acting


async def test_a_write_runs_once_dry_run_and_approval_are_loosened(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))

    report = await world.ushabti.once()

    assert world.write.calls == [{"action": "restart"}]
    assert world.events()[-4:] == ["verdict", "action", "proposal", "summons"]
    (action,) = world.of("action")
    assert action.verdict is Verdict.PASS
    assert action.payload["ok"] is True
    assert action.payload["kind"] == "write"
    assert report.answered[0].detail == "verdicts: 1 PASS"


async def test_every_write_that_ran_has_a_pass_verdict_recorded_before_it(tmp_path: Path) -> None:
    calls = [write_call(f"call-{n}") for n in range(1, 4)]
    provider = FakeProvider([proposing(*calls), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))
    await world.ushabti.once()

    entries = world.entries()
    actions = [i for i, e in enumerate(entries) if e.event is PapyrusEvent.ACTION]
    assert len(actions) == len(world.write.calls) == 3
    for index in actions:
        before = entries[index - 1]
        assert before.event is PapyrusEvent.VERDICT
        assert before.verdict is Verdict.PASS
        assert before.payload["call"] == entries[index].payload["call"]


async def test_a_write_that_needs_approval_is_escalated_and_not_run(tmp_path: Path) -> None:
    policy = {"dry_run": False, "require_approval_for": ["write"]}
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=policy))

    report = await world.ushabti.once()

    assert world.write.calls == []
    assert world.of("verdict")[0].verdict is Verdict.NEEDS_APPROVAL
    (summons,) = report.answered
    assert summons.state is SummonsState.ESCALATED
    assert summons.detail == "verdicts: 1 NEEDS_APPROVAL"
    # The API exposes it: the summons is known by id in its escalated state.
    assert world.ushabti.summons(summons.id) == summons
    assert provider.requests[1].messages[2].is_error


async def test_an_implement_outside_the_spell_is_rejected(tmp_path: Path) -> None:
    outside = [
        ToolCall(id="call-1", name="kubernetes.scale_deployment", arguments={"replicas": 0}),
        ToolCall(id="call-2", name="shell.exec", arguments={"command": "rm -rf /"}),
    ]
    provider = FakeProvider([proposing(*outside), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))
    await world.ushabti.once()

    verdicts = world.of("verdict")
    assert [v.verdict for v in verdicts] == [Verdict.REJECT, Verdict.REJECT]
    assert [v.payload["rule"] for v in verdicts] == ["not_listed", "not_listed"]
    assert verdicts[1].payload["kind"] is None
    assert world.of("action") == []
    assert provider.requests[1].messages[2].content.startswith("Rejected: ")


async def test_the_hourly_limit_holds_and_the_window_slides(tmp_path: Path) -> None:
    policy = {"dry_run": False, "require_approval_for": [], "max_actions_per_hour": 2}
    calls = [write_call(f"call-{n}") for n in range(1, 4)]
    provider = FakeProvider([proposing(*calls), DONE, proposing(write_call("call-4")), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=policy, maat=[]))

    world.ushabti.submit(operator_summons())
    await world.ushabti.answer_pending()
    assert [v.verdict for v in world.of("verdict")] == [Verdict.PASS, Verdict.PASS, Verdict.REJECT]
    assert world.of("verdict")[2].payload["rule"] == "rate_limited"
    assert len(world.write.calls) == 2

    world.clock.advance(minutes=61)
    world.ushabti.submit(Summons(id="op-2", source=SummonsSource.OPERATOR, reason="Again."))
    await world.ushabti.answer_pending()
    assert world.of("verdict")[3].verdict is Verdict.PASS
    assert len(world.write.calls) == 3


async def test_the_hourly_count_survives_a_restart(tmp_path: Path) -> None:
    policy = {"dry_run": False, "require_approval_for": [], "max_actions_per_hour": 1}
    spell = make_spell(weighing=policy, maat=[])
    first = World(tmp_path, provider=FakeProvider([proposing(write_call()), DONE]), spell=spell)
    first.ushabti.submit(operator_summons())
    await first.ushabti.answer_pending()
    assert len(first.write.calls) == 1

    restarted = World(tmp_path, provider=FakeProvider([proposing(write_call()), DONE]), spell=spell)
    restarted.ushabti.submit(operator_summons())
    await restarted.ushabti.answer_pending()
    assert restarted.write.calls == []
    assert restarted.of("verdict")[-1].payload["rule"] == "rate_limited"


async def test_a_listed_implement_missing_from_the_registry_is_rejected(
    tmp_path: Path,
) -> None:
    spell = make_spell(
        implements=["noop.read", "noop.write", "kubernetes.restart_deployment"],
        weighing=LOOSE,
        maat=[],
    )
    call = ToolCall(id="call-1", name="kubernetes.restart_deployment", arguments={})
    provider = FakeProvider([proposing(call), DONE])
    world = World(tmp_path, provider=provider, spell=spell)
    world.ushabti.submit(operator_summons())
    await world.ushabti.answer_pending()

    # The spell lists it, the weighing passes it, but this registry does not hold it.
    (verdict,) = world.of("verdict")
    assert verdict.verdict is Verdict.REJECT
    assert "not a registered implement" in str(verdict.payload["reason"])


async def test_an_implement_that_raises_becomes_a_failed_action(tmp_path: Path) -> None:
    class AlwaysPass:
        def weigh(self, request: WeighingRequest) -> WeighingResult:
            return WeighingResult(
                implement=request.implement, verdict=Verdict.PASS, rule="test", reason="test"
            )

    ghost = ToolCall(id="call-1", name="ghost.write", arguments={})
    provider = FakeProvider([proposing(ghost), DONE])
    world = World(tmp_path, provider=provider, weighing=AlwaysPass(), spell=make_spell(maat=[]))
    world.ushabti.submit(operator_summons())
    await world.ushabti.answer_pending()

    (action,) = world.of("action")
    assert action.payload["ok"] is False
    assert action.payload["error"] == "ghost.write is not a registered implement"
    assert provider.requests[1].messages[2].content.startswith("Failed: ")
    assert provider.requests[1].messages[2].is_error


async def test_a_custom_weighing_cannot_slip_a_write_past_the_implement_guard(
    tmp_path: Path,
) -> None:
    class WrongName:
        """Returns PASS, but for a different implement than the one proposed."""

        def weigh(self, request: WeighingRequest) -> WeighingResult:
            return WeighingResult(
                implement="noop.read", verdict=Verdict.PASS, rule="test", reason="test"
            )

    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, weighing=WrongName(), spell=make_spell(maat=[]))
    world.ushabti.submit(operator_summons())
    await world.ushabti.answer_pending()

    assert world.write.calls == []
    (action,) = world.of("action")
    assert action.payload["ok"] is False
    assert "the verdict is for noop.read" in str(action.payload["error"])


# Halt


async def test_halt_works_with_no_model_configured(tmp_path: Path) -> None:
    world = World(tmp_path, provider=None)
    assert world.ushabti.has_model is False

    assert world.ushabti.halt(by="test") is True
    assert world.ushabti.here_am_i().halted
    assert world.ushabti.halt() is False, "halting twice changes nothing"
    assert world.ushabti.here_am_i().halted is True

    (halt,) = world.of("halt")
    assert halt.payload == {"by": "test"}


async def test_a_halted_ushabti_keeps_observing_but_does_not_act(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))
    world.ushabti.halt()

    report = await world.ushabti.once()

    assert world.events() == ["halt", "vigil_tick", "observation", "isfet"]
    assert world.of("vigil_tick")[0].payload == {"tick": 1, "halted": True}
    assert len(report.isfet) == 1, "isfet is still seen and recorded"
    assert report.raised == ()
    assert provider.requests == [], "no model call is made while halted"
    assert world.write.calls == []
    assert world.ushabti.last_vigil == AT


async def test_halt_blocks_writes_even_when_it_lands_mid_summons(tmp_path: Path) -> None:
    world_box: list[World] = []

    class HaltsWhileThinking(FakeProvider):
        async def complete(
            self,
            *,
            system: str,
            messages: Sequence[Message],
            implements: Sequence[ImplementSpec],
        ) -> Completion:
            # The halt arrives while the model is still answering.
            world_box[0].ushabti.halt(by="test")
            return await super().complete(system=system, messages=messages, implements=implements)

    provider = HaltsWhileThinking([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))
    world_box.append(world)

    report = await world.ushabti.once()

    assert world.write.calls == [], "the write was proposed before the halt but must not run"
    (verdict,) = world.of("verdict")
    assert verdict.verdict is Verdict.REJECT
    assert verdict.payload["rule"] == "halted"
    assert len(provider.requests) == 1, "the model is not asked again once halted"
    assert report.answered[0].state is SummonsState.FAILED
    assert report.answered[0].detail == "the ushabti is halted"


async def test_a_halted_ushabti_leaves_summonses_queued_until_resumed(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE, maat=[]))
    world.ushabti.halt()
    world.ushabti.submit(operator_summons())

    assert await world.ushabti.answer_pending() == []
    pending = world.ushabti.summons("op-1")
    assert pending is not None
    assert pending.state is SummonsState.PENDING

    assert world.ushabti.resume(by="test") is True
    assert world.ushabti.resume() is False
    (answered,) = await world.ushabti.answer_pending()
    assert answered.state is SummonsState.ANSWERED
    assert world.write.calls == [{"action": "restart"}]
    assert world.events()[:3] == ["halt", "summons", "resume"]


async def test_halt_survives_a_restart_and_so_does_resume(tmp_path: Path) -> None:
    World(tmp_path).ushabti.halt()
    restarted = World(tmp_path)
    assert restarted.ushabti.halted is True, "a restart must not undo the kill switch"

    restarted.ushabti.resume()
    assert World(tmp_path).ushabti.halted is False


async def test_another_ushabtis_halt_in_a_shared_papyrus_is_ignored(tmp_path: Path) -> None:
    World(tmp_path, name="neighbour").ushabti.halt()
    assert World(tmp_path, name="me").ushabti.halted is False


async def test_drift_seen_while_halted_raises_a_summons_after_resume(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider)
    world.ushabti.halt()
    await world.ushabti.once()
    assert world.of("summons") == []

    world.ushabti.resume()
    report = await world.ushabti.once()
    assert len(report.raised) == 1


# The vigil


async def test_standing_drift_raises_one_summons_not_one_per_tick(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider)
    for _ in range(3):
        await world.ushabti.once()

    assert len(world.of("vigil_tick")) == 3
    assert len(world.of("observation")) == 3
    assert len(world.of("isfet")) == 1
    assert len(world.of("summons")) == 2, "one pending entry and one answered entry"


async def test_balance_restored_is_recorded_and_new_drift_raises_again(tmp_path: Path) -> None:
    read = ScriptedRead({"healthy": False}, {"healthy": True}, {"healthy": False})
    world = World(tmp_path, provider=FakeProvider(), read=read)
    for _ in range(3):
        await world.ushabti.once()

    drift = [e.event.value for e in world.entries() if e.event.value in ("isfet", "in_balance")]
    assert drift == ["isfet", "in_balance", "isfet"]
    assert world.of("in_balance")[0].payload == {
        "assertion": "service-healthy",
        "reason": "holds: healthy eq true",
    }


async def test_an_omen_raises_a_summons_while_ma_at_still_holds(tmp_path: Path) -> None:
    disk = {
        "id": "disk-has-room",
        "description": "The disk stays below 90 percent.",
        "observe": {"implement": "noop.read", "arguments": {"used_percent": 85}},
        "expect": {"path": "used_percent", "op": "lt", "value": 90},
        "omen": {"path": "used_percent", "op": "ge", "value": 80},
    }
    world = World(tmp_path, provider=FakeProvider(), spell=make_spell(maat=[disk]))
    report = await world.ushabti.once()

    assert world.events() == ["vigil_tick", "observation", "omen", "summons", "proposal", "summons"]
    assert report.isfet == ()
    assert report.omens[0].warning == "used_percent ge 80"
    assert "is predicted to break" in report.raised[0].reason
    assert report.raised[0].cause == report.omens[0].id
    assert report.answered[0].detail == "no implement calls were proposed"


async def test_an_observation_that_fails_is_recorded_and_raises_nothing(tmp_path: Path) -> None:
    class Broken(ScriptedRead):
        async def _run(self, arguments: object, field: object) -> None:
            raise NotImplementedError("noop.read is a scaffold stub in this test")

    world = World(tmp_path, provider=FakeProvider(), read=Broken())
    report = await world.ushabti.once()

    assert world.events() == ["vigil_tick", "observation"]
    assert world.of("observation")[0].payload == {
        "assertion": "service-healthy",
        "implement": "noop.read",
        "field": "test-field",
        "ok": False,
        "error": "noop.read is a scaffold stub in this test",
    }
    assert report.unknown == 1
    assert report.raised == ()


async def test_an_observation_that_cannot_be_compared_is_unknown(tmp_path: Path) -> None:
    world = World(tmp_path, provider=FakeProvider(), read=ScriptedRead({"other": 1}))
    report = await world.ushabti.once()
    assert report.unknown == 1
    assert world.events() == ["vigil_tick", "observation"]


async def test_the_vigil_never_runs_a_write_implement_to_observe(tmp_path: Path) -> None:
    # A spell built by hand that skips load_spell's check and observes through a write.
    sneaky = {**UNHEALTHY, "observe": {"implement": "noop.write"}}
    world = World(tmp_path, provider=FakeProvider(), spell=make_spell(maat=[sneaky]))
    await world.ushabti.once()

    assert world.write.calls == []
    (observation,) = world.of("observation")
    assert observation.payload["ok"] is False
    assert "was not weighed" in str(observation.payload["error"])


async def test_a_spell_with_only_prose_assertions_ticks_quietly(tmp_path: Path) -> None:
    prose = {"id": "all-is-well", "description": "Things look fine."}
    world = World(tmp_path, provider=FakeProvider(), spell=make_spell(maat=[prose]))
    report = await world.ushabti.once()
    assert world.events() == ["vigil_tick"]
    assert report.observed == 0


# Summonses from outside


async def test_a_submitted_summons_is_queued_once_and_answered(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call()), DONE])
    world = World(tmp_path, provider=provider, spell=make_spell(maat=[]))

    accepted = world.ushabti.submit(operator_summons().moved_to(SummonsState.ANSWERED, "forged"))
    assert accepted.state is SummonsState.PENDING, "a caller cannot pre-answer a summons"
    assert accepted.detail is None
    assert world.ushabti.submit(operator_summons()) == accepted, "the same id is accepted once"
    assert len(world.of("summons")) == 1

    (answered,) = await world.ushabti.answer_pending()
    assert answered.id == "op-1"
    assert answered.state is SummonsState.ANSWERED
    assert provider.requests[0].messages[0].content == (
        "You are summoned by the operator.\nRestart the service."
    )


async def test_the_summons_prompt_names_field_and_implement(tmp_path: Path) -> None:
    provider = FakeProvider()
    world = World(tmp_path, provider=provider, spell=make_spell(maat=[]))
    world.ushabti.submit(
        Summons(
            source=SummonsSource.OVERSEER,
            reason="Check the service.",
            field="test-field",
            implement="noop.read",
        )
    )
    await world.ushabti.answer_pending()
    assert provider.requests[0].messages[0].content == (
        "You are summoned by the overseer.\nCheck the service.\n"
        "Field: test-field\nImplement expected to be needed: noop.read"
    )


async def test_without_a_model_a_summons_fails_cleanly(tmp_path: Path) -> None:
    world = World(tmp_path, provider=None)
    report = await world.ushabti.once()
    assert report.answered[0].state is SummonsState.FAILED
    assert report.answered[0].detail == "no model is configured"
    assert world.events() == ["vigil_tick", "observation", "isfet", "summons", "summons"]


async def test_a_model_error_fails_the_summons(tmp_path: Path) -> None:
    class Down(FakeProvider):
        async def complete(self, **kwargs: object) -> Completion:
            raise ModelError("could not reach the model endpoint")

    world = World(tmp_path, provider=Down())
    report = await world.ushabti.once()
    assert report.answered[0].state is SummonsState.FAILED
    assert report.answered[0].detail == (
        "the model call failed: could not reach the model endpoint"
    )


async def test_a_refusal_fails_the_summons(tmp_path: Path) -> None:
    refusal = Completion(content="", stop_reason=StopReason.REFUSAL, tool_calls=(write_call(),))
    world = World(tmp_path, provider=FakeProvider([refusal]), spell=make_spell(weighing=LOOSE))
    report = await world.ushabti.once()
    assert report.answered[0].state is SummonsState.FAILED
    assert report.answered[0].detail == "the model declined to answer"
    assert world.of("verdict") == []
    assert world.write.calls == []


async def test_max_steps_bounds_how_long_the_model_may_go_on(tmp_path: Path) -> None:
    endless = FakeProvider(when_exhausted=proposing(write_call()))
    spell = make_spell(model={"provider": "fake", "max_steps": 3})
    world = World(tmp_path, provider=endless, spell=spell)
    report = await world.ushabti.once()

    assert len(endless.requests) == 3
    assert report.answered[0].detail == "verdicts: 3 DRY_RUN; stopped after 3 steps"


# Identity


async def test_here_am_i_describes_the_ushabti(tmp_path: Path) -> None:
    world = World(tmp_path, name="watcher-1")
    message = world.ushabti.here_am_i()
    assert message.name == "watcher-1"
    assert message.spell == "test-spell"
    assert message.mode.value == "solo"
    assert message.halted is False
    assert message.last_vigil is None
    assert message.fields == ("test-field",)
    assert message.implements == ("noop.read", "noop.write")
    assert message.version
    assert message.sent_at == AT

    await world.ushabti.tick()
    assert world.ushabti.here_am_i().last_vigil == AT
    assert world.of("vigil_tick")[0].ushabti == "watcher-1"


async def test_defaults_use_the_real_clock_registry_and_weighing(tmp_path: Path) -> None:
    from ushabti import JsonlPapyrus

    ushabti = Ushabti(make_spell(), papyrus=JsonlPapyrus(tmp_path / "p.jsonl"))
    report = await ushabti.once()
    assert report.isfet[0].detected_at.tzinfo is not None
    assert len(report.raised[0].id) == 32
    assert isinstance(ushabti._weighing, DefaultWeighing)


# The long-running loop


async def test_run_keeps_the_vigil_and_answers_summonses_until_stopped(tmp_path: Path) -> None:
    provider = FakeProvider()
    spell = make_spell(vigil={"interval": "20ms"}, maat=[])
    world = World(tmp_path, provider=provider, spell=spell, clock=Clock())
    stop = asyncio.Event()
    running = asyncio.ensure_future(world.ushabti.run(stop))

    async def until(condition: object) -> None:
        async with asyncio.timeout(5):
            while not condition():  # type: ignore[operator]
                await asyncio.sleep(0.005)

    await until(lambda: len(world.of("vigil_tick")) >= 2)
    world.ushabti.submit(operator_summons())
    await until(
        lambda: (
            (s := world.ushabti.summons("op-1")) is not None and s.state is SummonsState.ANSWERED
        )
    )

    stop.set()
    await asyncio.wait_for(running, timeout=5)
    assert len(provider.requests) == 1


async def test_run_survives_a_tick_that_raises(tmp_path: Path) -> None:
    spell = make_spell(vigil={"interval": "10ms"}, maat=[])
    world = World(tmp_path, provider=FakeProvider(), spell=spell)
    real_tick = world.ushabti.tick
    attempts = 0

    async def flaky() -> object:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("the disk hiccuped")
        return await real_tick()

    world.ushabti.tick = flaky  # type: ignore[method-assign, assignment]
    stop = asyncio.Event()
    running = asyncio.ensure_future(world.ushabti.run(stop))
    async with asyncio.timeout(5):
        while len(world.of("vigil_tick")) < 1:
            await asyncio.sleep(0.005)
    stop.set()
    await asyncio.wait_for(running, timeout=5)
    assert attempts >= 2
