"""JsonlPapyrus only ever appends."""

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ushabti import JsonlPapyrus, Papyrus, PapyrusEntry, PapyrusEvent, Verdict

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def entry(number: int, event: PapyrusEvent = PapyrusEvent.VIGIL_TICK) -> PapyrusEntry:
    return PapyrusEntry(timestamp=AT, ushabti="tester", event=event, payload={"n": number})


def test_each_append_adds_one_line_and_leaves_earlier_bytes_alone(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    papyrus = JsonlPapyrus(path)
    before = b""
    for number in range(5):
        papyrus.append(entry(number))
        after = path.read_bytes()
        assert after.startswith(before), "earlier content must never change"
        added = after[len(before) :]
        assert added.endswith(b"\n")
        assert added.count(b"\n") == 1
        assert json.loads(added)["payload"] == {"n": number}
        before = after


def test_an_entry_holds_timestamp_ushabti_event_payload_and_verdict(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    JsonlPapyrus(path).append(
        PapyrusEntry(
            timestamp=AT,
            ushabti="tester",
            event=PapyrusEvent.VERDICT,
            payload={"implement": "noop.write"},
            verdict=Verdict.DRY_RUN,
        )
    )
    assert json.loads(path.read_text()) == {
        "timestamp": "2026-01-02T03:04:05Z",
        "ushabti": "tester",
        "event": "verdict",
        "payload": {"implement": "noop.write"},
        "verdict": "DRY_RUN",
    }


def test_a_new_instance_keeps_what_is_already_there(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    JsonlPapyrus(path).append(entry(1))
    first = path.read_bytes()

    reopened = JsonlPapyrus(path)
    assert path.read_bytes() == first, "opening must not touch the file"
    reopened.append(entry(2))

    assert path.read_bytes().startswith(first)
    assert [e.payload["n"] for e in reopened.entries()] == [1, 2]


def test_foreign_content_is_never_truncated(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    path.write_text("not json at all\n")
    papyrus = JsonlPapyrus(path)
    papyrus.append(entry(1))
    assert path.read_text().startswith("not json at all\n")
    # The damaged line is skipped on read, never repaired by rewriting.
    assert [e.payload["n"] for e in papyrus.entries()] == [1]


def test_a_partial_last_line_is_finished_not_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    papyrus = JsonlPapyrus(path)
    papyrus.append(entry(1))
    with path.open("ab") as handle:
        handle.write(b'{"timestamp": "2026-01-')  # a crash mid-write
    torn = path.read_bytes()

    papyrus.append(entry(2))

    assert path.read_bytes().startswith(torn)
    assert [e.payload["n"] for e in papyrus.entries()] == [1, 2]


def test_the_file_is_opened_for_append_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []
    real_open = os.open

    def spy(path: str | Path, flags: int, mode: int = 0o777) -> int:
        seen.append(flags)
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", spy)
    JsonlPapyrus(tmp_path / "papyrus.jsonl").append(entry(1))

    assert len(seen) == 1
    assert seen[0] & os.O_APPEND
    assert not seen[0] & os.O_TRUNC
    assert not seen[0] & os.O_RDWR


def test_there_is_no_way_to_change_or_remove_entries() -> None:
    public = {name for name in vars(JsonlPapyrus) if not name.startswith("_")}
    assert public == {"append", "entries"}


def test_parent_directories_are_created(tmp_path: Path) -> None:
    path = tmp_path / "var" / "lib" / "ushabti" / "papyrus.jsonl"
    JsonlPapyrus(path).append(entry(1))
    assert path.exists()


def test_reading_a_missing_file_yields_nothing(tmp_path: Path) -> None:
    assert list(JsonlPapyrus(tmp_path / "absent.jsonl").entries()) == []


def test_entries_round_trip_in_order(tmp_path: Path) -> None:
    papyrus = JsonlPapyrus(tmp_path / "papyrus.jsonl")
    written = [entry(n, PapyrusEvent.ACTION) for n in range(3)]
    for item in written:
        papyrus.append(item)
    assert list(papyrus.entries()) == written


def test_concurrent_appends_stay_whole(tmp_path: Path) -> None:
    papyrus = JsonlPapyrus(tmp_path / "papyrus.jsonl")

    def write_twenty(number: int) -> None:
        for _ in range(20):
            papyrus.append(entry(number))

    threads = [threading.Thread(target=write_twenty, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(list(papyrus.entries())) == 160


def test_jsonl_papyrus_satisfies_the_protocol(tmp_path: Path) -> None:
    papyrus: Papyrus = JsonlPapyrus(tmp_path / "papyrus.jsonl")
    papyrus.append(entry(1))
    assert len(list(papyrus.entries())) == 1
