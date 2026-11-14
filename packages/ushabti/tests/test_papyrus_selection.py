"""Reading Papyrus through filters. Selection reads; it never touches the file."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ushabti import JsonlPapyrus, PapyrusEntry, PapyrusEvent, Verdict, select_entries

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def entry(
    seconds: int, event: PapyrusEvent, verdict: Verdict | None = None, **payload: Any
) -> PapyrusEntry:
    return PapyrusEntry(
        timestamp=AT + timedelta(seconds=seconds),
        ushabti="test",
        event=event,
        payload=payload,
        verdict=verdict,
    )


LOG = [
    entry(0, PapyrusEvent.OBSERVATION, assertion="memory", implement="noop.read", ok=True),
    entry(1, PapyrusEvent.ISFET, id="isfet-1", assertion="memory"),
    entry(2, PapyrusEvent.OBSERVATION, assertion="disk", implement="noop.read", ok=True),
    entry(3, PapyrusEvent.IN_BALANCE, assertion="disk"),
    entry(4, PapyrusEvent.VIGIL_TICK, tick=1),
    entry(5, PapyrusEvent.SUMMONS, id="s-1", source="vigil", state="pending"),
    entry(6, PapyrusEvent.PROPOSAL, summons="s-1", step=1),
    entry(7, PapyrusEvent.VERDICT, Verdict.DRY_RUN, summons="s-1", implement="noop.write"),
    entry(8, PapyrusEvent.SUMMONS, id="s-1", source="vigil", state="answered"),
    entry(9, PapyrusEvent.VIGIL_TICK, tick=2),
]


def events(selected: list[PapyrusEntry]) -> list[str]:
    return [item.event.value for item in selected]


def test_without_filters_everything_comes_back_oldest_first() -> None:
    assert select_entries(LOG) == LOG


def test_events_keep_only_those_kinds() -> None:
    ticks = select_entries(LOG, events=(PapyrusEvent.VIGIL_TICK,))
    assert [item.payload["tick"] for item in ticks] == [1, 2]

    mixed = select_entries(LOG, events={PapyrusEvent.PROPOSAL, PapyrusEvent.VERDICT})
    assert events(mixed) == ["proposal", "verdict"]


def test_since_keeps_the_moment_itself_and_treats_a_naive_moment_as_utc() -> None:
    aware = select_entries(LOG, since=AT + timedelta(seconds=8))
    assert events(aware) == ["summons", "vigil_tick"]

    naive = select_entries(LOG, since=(AT + timedelta(seconds=8)).replace(tzinfo=None))
    assert naive == aware

    assert select_entries(LOG, since=AT + timedelta(days=1)) == []


def test_a_summons_filter_follows_one_summons_from_raised_to_answered() -> None:
    thread = select_entries(LOG, summons="s-1")
    assert events(thread) == ["summons", "proposal", "verdict", "summons"]
    assert select_entries(LOG, summons="s-2") == []


def test_an_assertion_filter_keeps_its_observations_and_outcomes() -> None:
    memory = select_entries(LOG, assertion="memory")
    assert events(memory) == ["observation", "isfet"]
    disk = select_entries(LOG, assertion="disk")
    assert events(disk) == ["observation", "in_balance"]


def test_filters_combine() -> None:
    only = select_entries(LOG, events=(PapyrusEvent.VERDICT,), summons="s-1")
    assert len(only) == 1
    assert only[0].verdict is Verdict.DRY_RUN
    assert select_entries(LOG, events=(PapyrusEvent.ISFET,), summons="s-1") == []


def test_the_limit_keeps_the_newest_entries() -> None:
    assert select_entries(LOG, limit=2) == LOG[-2:]
    assert select_entries(LOG, limit=1, events=(PapyrusEvent.OBSERVATION,)) == [LOG[2]]
    assert select_entries(LOG, limit=100) == LOG


def test_a_limit_below_one_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        select_entries(LOG, limit=0)


def test_an_iterator_is_consumed_once_and_in_order() -> None:
    assert select_entries(iter(LOG), events=(PapyrusEvent.SUMMONS,)) == [LOG[5], LOG[8]]


def test_selecting_from_a_file_reads_and_leaves_every_byte_alone(tmp_path: Path) -> None:
    papyrus = JsonlPapyrus(tmp_path / "papyrus.jsonl")
    for item in LOG:
        papyrus.append(item)
    before = papyrus.path.read_bytes()

    thread = select_entries(papyrus.entries(), summons="s-1", limit=3)

    assert events(thread) == ["proposal", "verdict", "summons"]
    assert papyrus.path.read_bytes() == before
