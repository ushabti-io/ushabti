"""The HereAmI, Summons, Omen, Isfet, and PapyrusEntry models."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

import ushabti
from ushabti import (
    HereAmI,
    Isfet,
    Mode,
    Omen,
    PapyrusEntry,
    PapyrusEvent,
    Summons,
    SummonsSource,
    SummonsState,
    Verdict,
)
from ushabti._base import format_duration, new_id, parse_duration, utc_now

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def test_here_am_i_carries_what_the_health_endpoint_promises() -> None:
    message = HereAmI(
        name="k3s-watcher-1",
        spell="k3s-watcher",
        mode=Mode.OVERSEEN,
        halted=False,
        last_vigil=AT,
        fields=("homelab-k3s",),
        implements=("kubernetes.list_pods",),
        url="http://agent.example.invalid:8080",
        sent_at=AT,
    )
    assert message.model_dump(mode="json") == {
        "name": "k3s-watcher-1",
        "spell": "k3s-watcher",
        "mode": "overseen",
        "halted": False,
        "last_vigil": "2026-01-02T03:04:05Z",
        "fields": ["homelab-k3s"],
        "implements": ["kubernetes.list_pods"],
        "url": "http://agent.example.invalid:8080",
        "version": None,
        "sent_at": "2026-01-02T03:04:05Z",
    }
    assert HereAmI.model_validate_json(message.model_dump_json()) == message


def test_here_am_i_before_the_first_vigil() -> None:
    message = HereAmI(name="a", spell="s", mode=Mode.SOLO, halted=True)
    assert message.last_vigil is None
    assert message.sent_at.tzinfo is not None


def test_summons_defaults_and_round_trip() -> None:
    summons = Summons(source=SummonsSource.OPERATOR, reason="Restart the web deployment.")
    assert summons.state is SummonsState.PENDING
    assert len(summons.id) == 32
    assert summons.created_at.tzinfo is not None
    assert Summons.model_validate_json(summons.model_dump_json()) == summons


def test_summons_ids_are_unique() -> None:
    first = Summons(source=SummonsSource.VIGIL, reason="a")
    second = Summons(source=SummonsSource.VIGIL, reason="a")
    assert first.id != second.id


def test_summons_moves_between_states_without_mutating() -> None:
    summons = Summons(source=SummonsSource.OVERSEER, reason="Scale down.", field="homelab-k3s")
    answered = summons.moved_to(SummonsState.ANSWERED, "1 call recorded as a dry run")
    assert summons.state is SummonsState.PENDING
    assert answered.state is SummonsState.ANSWERED
    assert answered.detail == "1 call recorded as a dry run"
    assert answered.id == summons.id
    assert answered.field == "homelab-k3s"


def test_summons_sources_and_states_match_the_glossary() -> None:
    assert {source.value for source in SummonsSource} == {"vigil", "overseer", "operator"}
    assert [state.value for state in SummonsState] == [
        "pending",
        "answering",
        "answered",
        "escalated",
        "failed",
    ]


def test_omen_and_isfet_round_trip() -> None:
    omen = Omen(
        id="o1",
        assertion="cert-valid",
        field="ingress",
        description="The certificate is valid for more than 14 days.",
        observed=9,
        warning="days_left lt 14",
        predicted_at=AT,
    )
    isfet = Isfet(
        id="i1",
        assertion="deployments-ready",
        description="Every deployment is ready.",
        observed={"ready": 1, "desired": 3},
        expected="ready eq 3",
        detected_at=AT,
    )
    assert Omen.model_validate_json(omen.model_dump_json()) == omen
    assert Isfet.model_validate_json(isfet.model_dump_json()) == isfet
    assert isfet.field is None


def test_papyrus_entry_round_trip_with_and_without_a_verdict() -> None:
    plain = PapyrusEntry(timestamp=AT, ushabti="a", event=PapyrusEvent.VIGIL_TICK)
    judged = PapyrusEntry(
        timestamp=AT,
        ushabti="a",
        event=PapyrusEvent.VERDICT,
        payload={"implement": "noop.write", "arguments": {"n": [1, 2]}},
        verdict=Verdict.NEEDS_APPROVAL,
    )
    assert plain.payload == {}
    assert plain.verdict is None
    for entry in (plain, judged):
        assert PapyrusEntry.model_validate_json(entry.model_dump_json()) == entry


def test_papyrus_events_cover_the_audited_steps() -> None:
    assert {"omen", "isfet", "summons", "verdict", "action"} <= {e.value for e in PapyrusEvent}


def test_wire_models_are_frozen() -> None:
    summons = Summons(source=SummonsSource.VIGIL, reason="a")
    with pytest.raises(ValidationError, match="frozen"):
        summons.state = SummonsState.FAILED  # type: ignore[misc]


def test_wire_models_ignore_keys_from_a_newer_writer() -> None:
    message = HereAmI.model_validate(
        {"name": "a", "spell": "s", "mode": "solo", "halted": False, "added_in_a_later_version": 1}
    )
    assert message.name == "a"


def test_wire_models_still_reject_wrong_values() -> None:
    with pytest.raises(ValidationError):
        Summons.model_validate({"source": "passerby", "reason": "x"})


def test_durations() -> None:
    assert parse_duration("90s") == timedelta(seconds=90)
    assert parse_duration(" 2h ") == timedelta(hours=2)
    assert parse_duration(timedelta(seconds=5)) == timedelta(seconds=5)
    assert format_duration(timedelta(seconds=90)) == "90s"
    assert format_duration(timedelta(minutes=5)) == "5m"
    assert format_duration(timedelta(hours=48)) == "2d"
    assert format_duration(timedelta(milliseconds=250)) == "250ms"
    with pytest.raises(ValueError, match="not a boolean"):
        parse_duration(True)


def test_helpers() -> None:
    assert utc_now().tzinfo is UTC
    assert new_id() != new_id()


def test_the_public_api_is_exported_and_versioned() -> None:
    assert ushabti.__version__
    for name in ushabti.__all__:
        assert hasattr(ushabti, name), name
    assert list(ushabti.__all__) == sorted(ushabti.__all__)
