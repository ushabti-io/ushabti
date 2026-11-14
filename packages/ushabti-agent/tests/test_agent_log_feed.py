"""The log feed: what the vigil says in the process log, and what it keeps out of it."""

import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
import structlog
from structlog.testing import capture_logs

from agent_testkit import LOOSE, World, make_spell, proposing, write_call
from ushabti import LogFormat, PapyrusEvent, Verdict, configure_logging
from ushabti.models.fake import FakeProvider
from ushabti_agent.runtime import FEED_TEXT_LIMIT, feed_fields

SECRET = "sk-live-do-not-leak-0123456789"
LONG = "The model explains at length. " * 100


@pytest.fixture(autouse=True)
def plain_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def events(captured: Sequence[Mapping[str, Any]]) -> list[str]:
    return [line["event"] for line in captured]


def only(captured: Sequence[Mapping[str, Any]], event: str) -> list[Mapping[str, Any]]:
    return [line for line in captured if line["event"] == event]


async def test_one_tick_with_drift_tells_the_whole_story(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call(), content=LONG)])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))

    with capture_logs() as captured:
        await world.ushabti.once()

    seen = events(captured)
    for expected in (
        "observation",
        "isfet",
        "summons",
        "vigil_tick",
        "proposal",
        "verdict",
        "action",
    ):
        assert expected in seen, expected
    assert seen.index("observation") < seen.index("isfet") < seen.index("vigil_tick")
    assert seen.index("vigil_tick") < seen.index("proposal") < seen.index("verdict")


async def test_the_feed_carries_the_story_and_not_the_data(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call(replicas=3), content=LONG)])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))

    with capture_logs() as captured:
        await world.ushabti.once()

    observation = only(captured, "observation")[0]
    assert observation["ok"] is True
    assert "data" not in observation

    isfet = only(captured, "isfet")[0]
    assert isfet["log_level"] == "warning"
    assert isfet["assertion"] == world.of("isfet")[0].payload["assertion"]

    proposal = only(captured, "proposal")[0]
    assert proposal["calls"] == ["noop.write"]
    assert proposal["content"].endswith(" [cut]")
    assert len(proposal["content"]) == FEED_TEXT_LIMIT + len(" [cut]")

    verdict = only(captured, "verdict")[0]
    assert verdict["verdict"] == Verdict.PASS.value
    assert verdict["implement"] == "noop.write"
    assert "arguments" not in verdict

    action = only(captured, "action")[0]
    assert action["ok"] is True
    assert "result" not in action

    tick = only(captured, "vigil_tick")[0]
    assert (tick["observed"], tick["isfet"], tick["raised"]) == (1, 1, 1)

    for line in captured:
        assert line["ushabti"] == world.ushabti.name


async def test_a_summons_that_ends_badly_is_a_warning(tmp_path: Path) -> None:
    spell = make_spell(weighing={"dry_run": False, "require_approval_for": ["write"]})
    world = World(tmp_path, provider=FakeProvider([proposing(write_call())]), spell=spell)

    with capture_logs() as captured:
        await world.ushabti.once()

    states = [(line["state"], line["log_level"]) for line in only(captured, "summons")]
    assert ("pending", "info") in states
    assert ("escalated", "warning") in states


async def test_nothing_from_the_environment_reaches_the_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_API_KEY", SECRET)
    provider = FakeProvider([proposing(write_call(token="${TEST_API_KEY}"))])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))

    with capture_logs() as captured:
        await world.ushabti.once()

    assert SECRET not in json.dumps(captured, default=str)


async def test_the_warning_level_keeps_only_the_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("WARNING", LogFormat.JSON)
    provider = FakeProvider([proposing(write_call())])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))

    await world.ushabti.once()
    world.ushabti.halt()

    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert {line["event"] for line in lines} == {"isfet", "halted"}


def test_halt_resume_and_ticks_are_not_fed_twice() -> None:
    assert feed_fields(PapyrusEvent.HALT, {"by": "api"}, None) is None
    assert feed_fields(PapyrusEvent.RESUME, {"by": "api"}, None) is None
    assert feed_fields(PapyrusEvent.VIGIL_TICK, {"tick": 1}, None) is None


def test_long_values_are_cut_and_short_ones_kept() -> None:
    fields = feed_fields(
        PapyrusEvent.ISFET,
        {"assertion": "memory", "observed": {"value": "x" * 600}, "expected": "value lt 90"},
        None,
    )
    assert fields is not None
    assert fields["expected"] == "value lt 90"
    assert isinstance(fields["observed"], str)
    assert fields["observed"].endswith(" [cut]")
    assert len(fields["observed"]) == FEED_TEXT_LIMIT + len(" [cut]")

    short = feed_fields(PapyrusEvent.ISFET, {"observed": {"value": 84.3}}, None)
    assert short == {"observed": {"value": 84.3}}
