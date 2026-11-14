"""Papyrus: the append-only audit log."""

import os
import threading
from collections import deque
from collections.abc import Collection, Iterable, Iterator
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Protocol

import pydantic
import structlog
from pydantic import JsonValue

from ushabti._base import WireModel
from ushabti.weighing import Verdict

_log = structlog.get_logger(__name__)

SELECTION_LIMIT: Final = 200
"""How many entries ``select_entries`` keeps when no limit is given."""


class PapyrusEvent(StrEnum):
    """What a Papyrus entry records."""

    VIGIL_TICK = "vigil_tick"
    OBSERVATION = "observation"
    ISFET = "isfet"
    OMEN = "omen"
    IN_BALANCE = "in_balance"
    SUMMONS = "summons"
    PROPOSAL = "proposal"
    VERDICT = "verdict"
    ACTION = "action"
    HALT = "halt"
    RESUME = "resume"


class PapyrusEntry(WireModel):
    """One line of the audit log."""

    timestamp: datetime
    """When the event happened."""

    ushabti: str
    """Name of the ushabti that recorded it."""

    event: PapyrusEvent
    """What happened."""

    payload: dict[str, JsonValue] = pydantic.Field(default_factory=dict)
    """Details of the event."""

    verdict: Verdict | None = None
    """The verdict of the weighing, when the event had one."""


class Papyrus(Protocol):
    """An append-only audit log. Entries can be added and read, never changed."""

    def append(self, entry: PapyrusEntry) -> None: ...

    def entries(self) -> Iterator[PapyrusEntry]: ...


def select_entries(
    entries: Iterable[PapyrusEntry],
    *,
    events: Collection[PapyrusEvent] = (),
    since: datetime | None = None,
    summons: str | None = None,
    assertion: str | None = None,
    limit: int = SELECTION_LIMIT,
) -> list[PapyrusEntry]:
    """The entries that match every given filter, oldest first.

    ``events`` keeps only those kinds; ``since`` drops entries before a moment
    (a naive moment is taken as UTC); ``summons`` keeps the summons itself and
    every proposal, verdict, and action made for it; ``assertion`` keeps the
    observations and outcomes of one assertion. When more than ``limit``
    entries match, the newest are kept. This only reads: it is how the API
    looks at the log.
    """
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if since is not None and since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    kept: deque[PapyrusEntry] = deque(maxlen=limit)
    for entry in entries:
        if events and entry.event not in events:
            continue
        if since is not None and entry.timestamp < since:
            continue
        if summons is not None and _summons_of(entry) != summons:
            continue
        if assertion is not None and entry.payload.get("assertion") != assertion:
            continue
        kept.append(entry)
    return list(kept)


def _summons_of(entry: PapyrusEntry) -> JsonValue:
    """The summons an entry belongs to: its own id for a summons event, else the payload's."""
    if entry.event is PapyrusEvent.SUMMONS:
        return entry.payload.get("id")
    return entry.payload.get("summons")


class JsonlPapyrus:
    """Papyrus as a JSON Lines file: one entry per line, oldest first.

    The file is only ever opened for appending or for reading. Nothing here
    rewrites, truncates, or deletes it.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: PapyrusEntry) -> None:
        """Write one entry at the end of the file and flush it to disk."""
        line = entry.model_dump_json() + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # O_APPEND makes the kernel place every write at the end of the file,
            # whatever any other writer does. There is no seek and no truncate.
            descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
            try:
                if self._ends_mid_line(descriptor):
                    # A crash left a partial line. Finish it so this entry stays whole.
                    line = "\n" + line
                os.write(descriptor, line.encode("utf-8"))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def entries(self) -> Iterator[PapyrusEntry]:
        """Read every entry, oldest first. Damaged lines are skipped and logged."""
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    yield PapyrusEntry.model_validate_json(text)
                except pydantic.ValidationError:
                    _log.warning("papyrus_line_unreadable", path=str(self.path), line=number)

    def _ends_mid_line(self, descriptor: int) -> bool:
        size = os.fstat(descriptor).st_size
        if size == 0:
            return False
        with self.path.open("rb") as handle:
            handle.seek(size - 1)
            return handle.read(1) != b"\n"
