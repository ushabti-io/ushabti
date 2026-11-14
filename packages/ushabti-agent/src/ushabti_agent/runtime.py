"""The agent runtime: one ushabti running one spell.

The vigil (the monitoring loop) observes, compares what it sees to Ma'at (the
desired state), and raises a summons (a unit of work) when something is wrong
or about to be. A model proposes implement calls for each summons, and every
call goes through the weighing (the safety gate) before anything runs. Each
step is written to Papyrus (the append-only audit log).
"""

import asyncio
import json
import uuid
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime, timedelta
from typing import Final

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge
from pydantic import JsonValue

from ushabti import (
    DefaultWeighing,
    Field,
    HereAmI,
    ImplementKind,
    ImplementRegistry,
    ImplementSpec,
    Isfet,
    Maat,
    MaatAssertion,
    MaatStatus,
    Message,
    Mode,
    ModelError,
    ModelProvider,
    Omen,
    Papyrus,
    PapyrusEntry,
    PapyrusEvent,
    Spell,
    StopReason,
    Summons,
    SummonsSource,
    SummonsState,
    ToolCall,
    Verdict,
    Weighing,
    WeighingRequest,
    WeighingResult,
    default_registry,
    select_entries,
)
from ushabti_agent import __version__

_log = structlog.get_logger(__name__)

_HOUR = timedelta(hours=1)
_DRIFTING = (MaatStatus.ISFET, MaatStatus.OMEN)

FEED_TEXT_LIMIT: Final = 500
"""Longest text the log feed carries. Papyrus keeps the whole of it."""

# The log feed: what each Papyrus event says in the process log, so a log
# pipeline shows the vigil as it happens. Only the keys that tell the story.
# Never the data an implement returned, the arguments a call carried, or the
# result an action produced: those stay in Papyrus. Ticks are logged with
# their counts at the end of the tick; halt and resume log on their own.
_FEED: Final[dict[PapyrusEvent, tuple[str, ...]]] = {
    PapyrusEvent.OBSERVATION: ("assertion", "implement", "field", "ok", "error"),
    PapyrusEvent.ISFET: ("assertion", "field", "observed", "expected"),
    PapyrusEvent.OMEN: ("assertion", "field", "observed", "warning"),
    PapyrusEvent.IN_BALANCE: ("assertion", "reason"),
    PapyrusEvent.SUMMONS: ("id", "source", "state", "reason", "detail", "field"),
    PapyrusEvent.PROPOSAL: ("summons", "step", "stop_reason", "content", "calls"),
    PapyrusEvent.VERDICT: ("summons", "implement", "kind", "rule", "reason"),
    PapyrusEvent.ACTION: ("summons", "implement", "kind", "ok", "error"),
}
_FEED_WARNINGS: Final = frozenset({"failed", "escalated"})
"""Summons states worth a warning rather than an info line."""


def feed_fields(
    event: PapyrusEvent, payload: dict[str, JsonValue], verdict: Verdict | None
) -> dict[str, JsonValue] | None:
    """What the log feed says about a Papyrus entry, or None when it says nothing."""
    keys = _FEED.get(event)
    if keys is None:
        return None
    fields: dict[str, JsonValue] = {}
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if key == "calls" and isinstance(value, list):
            # The names of the proposed calls, never their arguments.
            value = [call.get("name") if isinstance(call, dict) else None for call in value]
        fields[key] = _cut(value)
    if verdict is not None:
        fields["verdict"] = verdict.value
    return fields


def _cut(value: JsonValue) -> JsonValue:
    """A value short enough for a log line. Longer text ends in a marker."""
    if isinstance(value, str):
        return value if len(value) <= FEED_TEXT_LIMIT else value[:FEED_TEXT_LIMIT] + " [cut]"
    if isinstance(value, dict | list):
        text = json.dumps(value)
        return value if len(text) <= FEED_TEXT_LIMIT else text[:FEED_TEXT_LIMIT] + " [cut]"
    return value


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Metrics:
    """Prometheus metrics for one ushabti, in a registry of their own."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.vigil_ticks = Counter(
            "ushabti_vigil_ticks", "Vigil ticks completed.", registry=self.registry
        )
        self.isfet = Counter(
            "ushabti_isfet", "Times an assertion was found broken.", registry=self.registry
        )
        self.omens = Counter(
            "ushabti_omens", "Times an assertion was predicted to break.", registry=self.registry
        )
        self.verdicts = Counter(
            "ushabti_verdicts",
            "Verdicts of the weighing, by type.",
            ["verdict"],
            registry=self.registry,
        )
        self.actions = Counter(
            "ushabti_actions",
            "Implements that were run, by kind.",
            ["kind"],
            registry=self.registry,
        )
        self.halted = Gauge(
            "ushabti_halted", "1 when the ushabti is halted, else 0.", registry=self.registry
        )
        for verdict in Verdict:
            self.verdicts.labels(verdict=verdict.value)
        for kind in ImplementKind:
            self.actions.labels(kind=kind.value)


@dataclass(frozen=True, slots=True)
class TickReport:
    """What one vigil tick found."""

    tick: int
    halted: bool
    observed: int = 0
    unknown: int = 0
    isfet: tuple[Isfet, ...] = ()
    omens: tuple[Omen, ...] = ()
    raised: tuple[Summons, ...] = ()
    answered: tuple[Summons, ...] = dataclass_field(default=())


class Ushabti:
    """One agent instance running one spell."""

    def __init__(
        self,
        spell: Spell,
        *,
        papyrus: Papyrus,
        provider: ModelProvider | None = None,
        registry: ImplementRegistry | None = None,
        weighing: Weighing | None = None,
        name: str | None = None,
        mode: Mode = Mode.SOLO,
        url: str | None = None,
        clock: Callable[[], datetime] = utc_now,
        ids: Callable[[], str] = new_id,
    ) -> None:
        self.spell = spell
        self.name = name or spell.name
        self.mode = mode
        self.url = url
        self.metrics = Metrics()
        self._papyrus = papyrus
        self._provider = provider
        self._registry = default_registry() if registry is None else registry
        self._weighing: Weighing = DefaultWeighing() if weighing is None else weighing
        self._clock = clock
        self._ids = ids
        self._maat = Maat(spell.spec.maat)
        self._halted = False
        self._tick = 0
        self._last_vigil: datetime | None = None
        self._statuses: dict[str, MaatStatus] = {}
        self._writes: deque[datetime] = deque()
        self._summonses: dict[str, Summons] = {}
        self._pending: deque[str] = deque()
        self._working = asyncio.Lock()
        self._wake = asyncio.Event()
        self._restore()

    # State

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def last_vigil(self) -> datetime | None:
        return self._last_vigil

    @property
    def has_model(self) -> bool:
        return self._provider is not None

    def here_am_i(self) -> HereAmI:
        """The answer of this ushabti: who it is and how it is doing."""
        return HereAmI(
            name=self.name,
            spell=self.spell.name,
            mode=self.mode,
            halted=self._halted,
            last_vigil=self._last_vigil,
            fields=tuple(field.name for field in self.spell.spec.fields),
            implements=self.spell.spec.implements,
            url=self.url,
            version=__version__,
            sent_at=self._clock(),
        )

    def summons(self, summons_id: str) -> Summons | None:
        """The summons with this id in its current state, if this ushabti knows it."""
        return self._summonses.get(summons_id)

    def summonses(self, *, limit: int = 200) -> list[Summons]:
        """The summonses this ushabti remembers, newest first."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        ordered = sorted(self._summonses.values(), key=lambda s: s.created_at, reverse=True)
        return ordered[:limit]

    def papyrus_entries(
        self,
        *,
        events: Collection[PapyrusEvent] = (),
        since: datetime | None = None,
        summons: str | None = None,
        assertion: str | None = None,
        limit: int = 200,
    ) -> list[PapyrusEntry]:
        """Read Papyrus (the audit log) through the same filters the API offers.

        Oldest first, newest kept when the limit cuts. This reads the log and
        nothing else: Papyrus has no way to change or remove an entry.
        """
        return select_entries(
            self._papyrus.entries(),
            events=events,
            since=since,
            summons=summons,
            assertion=assertion,
            limit=limit,
        )

    # Halt and resume. Neither touches the model.

    def halt(self, by: str = "operator") -> bool:
        """Stop acting at once. Observing continues. Returns False if already halted."""
        if self._halted:
            return False
        self._halted = True
        self.metrics.halted.set(1)
        self._record(PapyrusEvent.HALT, {"by": by})
        _log.warning("halted", ushabti=self.name, by=by)
        return True

    def resume(self, by: str = "operator") -> bool:
        """Act again. Returns False if the ushabti was not halted."""
        if not self._halted:
            return False
        self._halted = False
        self.metrics.halted.set(0)
        self._record(PapyrusEvent.RESUME, {"by": by})
        # Forget what was seen while halted, so standing drift raises a summons again.
        self._statuses.clear()
        self._wake.set()
        _log.warning("resumed", ushabti=self.name, by=by)
        return True

    # The vigil

    async def tick(self) -> TickReport:
        """One vigil tick: observe, compare to Ma'at, and raise a summons for new drift.

        A halted ushabti still observes and records what it finds, but raises
        no summons, so no model is called.
        """
        async with self._working:
            self._tick += 1
            halted = self._halted
            self._record(PapyrusEvent.VIGIL_TICK, {"tick": self._tick, "halted": halted})
            observed = unknown = 0
            found_isfet: list[Isfet] = []
            found_omens: list[Omen] = []
            raised: list[Summons] = []

            for assertion in self._maat.checkable:
                field = self.spell.field_named(assertion.field)
                ok, data = await self._observe(assertion, field)
                observed += 1
                if not ok:
                    unknown += 1
                    self._statuses[assertion.id] = MaatStatus.UNKNOWN
                    continue
                outcome = self._maat.evaluate(
                    assertion,
                    data,
                    field=None if field is None else field.name,
                    at=self._clock(),
                    ids=self._ids,
                )
                previous = self._statuses.get(assertion.id)
                self._statuses[assertion.id] = outcome.status
                if outcome.status is MaatStatus.UNKNOWN:
                    unknown += 1
                elif outcome.isfet is not None and previous is not MaatStatus.ISFET:
                    found_isfet.append(outcome.isfet)
                    self.metrics.isfet.inc()
                    self._record(PapyrusEvent.ISFET, outcome.isfet.model_dump(mode="json"))
                    if not halted:
                        raised.append(self._summon_for_isfet(outcome.isfet))
                elif outcome.omen is not None and previous is not MaatStatus.OMEN:
                    found_omens.append(outcome.omen)
                    self.metrics.omens.inc()
                    self._record(PapyrusEvent.OMEN, outcome.omen.model_dump(mode="json"))
                    if not halted:
                        raised.append(self._summon_for_omen(outcome.omen))
                elif outcome.status is MaatStatus.IN_BALANCE and previous in _DRIFTING:
                    self._record(
                        PapyrusEvent.IN_BALANCE,
                        {"assertion": assertion.id, "reason": outcome.reason},
                    )

            self._last_vigil = self._clock()
            self.metrics.vigil_ticks.inc()
            _log.info(
                "vigil_tick",
                ushabti=self.name,
                tick=self._tick,
                halted=halted,
                observed=observed,
                unknown=unknown,
                isfet=len(found_isfet),
                omens=len(found_omens),
                raised=len(raised),
            )
            return TickReport(
                tick=self._tick,
                halted=halted,
                observed=observed,
                unknown=unknown,
                isfet=tuple(found_isfet),
                omens=tuple(found_omens),
                raised=tuple(raised),
            )

    async def once(self) -> TickReport:
        """One tick, then answer every summons it raised. For ``--once`` and tests."""
        report = await self.tick()
        answered = await self.answer_pending()
        return TickReport(
            tick=report.tick,
            halted=report.halted,
            observed=report.observed,
            unknown=report.unknown,
            isfet=report.isfet,
            omens=report.omens,
            raised=report.raised,
            answered=tuple(answered),
        )

    async def run(self, stop: asyncio.Event) -> None:
        """Keep the vigil until ``stop`` is set, answering summonses as they arrive."""
        interval = self.spell.spec.vigil.interval.total_seconds()
        loop = asyncio.get_running_loop()
        next_tick = loop.time()
        while not stop.is_set():
            self._wake.clear()
            try:
                if loop.time() >= next_tick:
                    next_tick = loop.time() + interval
                    await self.tick()
                await self.answer_pending()
            except Exception:
                # One bad tick must not end the vigil. The next one tries again.
                _log.exception("vigil_tick_failed", ushabti=self.name)
            await self._sleep(stop, max(0.0, next_tick - loop.time()))

    async def _sleep(self, stop: asyncio.Event, seconds: float) -> None:
        waiters = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self._wake.wait())]
        _, unfinished = await asyncio.wait(
            waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
        )
        for waiter in unfinished:
            waiter.cancel()

    async def _observe(
        self, assertion: MaatAssertion, field: Field | None
    ) -> tuple[bool, JsonValue]:
        observe = assertion.observe
        if observe is None:  # pragma: no cover  (checkable assertions always have one)
            return False, None
        payload: dict[str, JsonValue] = {
            "assertion": assertion.id,
            "implement": observe.implement,
            "field": None if field is None else field.name,
        }
        implement = self._registry.find(observe.implement)
        try:
            if implement is None:
                raise LookupError(f"{observe.implement} is not a registered implement")
            # No verdict is passed: the implement's own guard lets a read observe
            # and refuses anything that could write.
            data = await implement.run(observe.arguments, field=field)
        except Exception as error:
            # A stub, an outage, a bad argument: all mean "could not observe".
            reason = str(error) or type(error).__name__
            self._record(PapyrusEvent.OBSERVATION, {**payload, "ok": False, "error": reason})
            return False, None
        self._record(PapyrusEvent.OBSERVATION, {**payload, "ok": True, "data": data})
        return True, data

    # Summonses

    def submit(self, summons: Summons) -> Summons:
        """Accept a summons from an overseer or an operator and queue it.

        Submitting the same id twice is harmless: the first one stands.
        """
        known = self._summonses.get(summons.id)
        if known is not None:
            return known
        accepted = summons.moved_to(SummonsState.PENDING)
        self._enqueue(accepted)
        return accepted

    async def answer_pending(self) -> list[Summons]:
        """Answer queued summonses, oldest first. A halted ushabti leaves them queued."""
        answered: list[Summons] = []
        while self._pending and not self._halted:
            async with self._working:
                if not self._pending or self._halted:
                    break
                summons = self._summonses[self._pending.popleft()]
                answered.append(await self._answer(summons))
        return answered

    def _enqueue(self, summons: Summons) -> None:
        self._summonses[summons.id] = summons
        self._pending.append(summons.id)
        self._record(PapyrusEvent.SUMMONS, summons.model_dump(mode="json"))
        self._wake.set()

    def _summon_for_isfet(self, isfet: Isfet) -> Summons:
        summons = Summons(
            id=self._ids(),
            source=SummonsSource.VIGIL,
            reason=(
                f"Ma'at assertion {isfet.assertion} does not hold. {isfet.description} "
                f"Expected {isfet.expected}; observed {json.dumps(isfet.observed)}."
            ),
            field=isfet.field,
            cause=isfet.id,
            created_at=self._clock(),
        )
        self._enqueue(summons)
        return summons

    def _summon_for_omen(self, omen: Omen) -> Summons:
        summons = Summons(
            id=self._ids(),
            source=SummonsSource.VIGIL,
            reason=(
                f"Ma'at assertion {omen.assertion} still holds but is predicted to break. "
                f"{omen.description} Warning met: {omen.warning}; "
                f"observed {json.dumps(omen.observed)}."
            ),
            field=omen.field,
            cause=omen.id,
            created_at=self._clock(),
        )
        self._enqueue(summons)
        return summons

    async def _answer(self, summons: Summons) -> Summons:
        """Ask the model what to do, and put every call it proposes through the weighing."""
        summons = self._remember(summons.moved_to(SummonsState.ANSWERING))
        if self._provider is None:
            return self._finish(summons, SummonsState.FAILED, "no model is configured")

        system = self._system_prompt()
        specs = self._implement_specs()
        messages = [Message.user(self._summons_prompt(summons))]
        tally: dict[Verdict, int] = dict.fromkeys(Verdict, 0)
        steps = self.spell.spec.model.max_steps
        out_of_steps = True

        for step in range(1, steps + 1):
            if self._halted:
                return self._finish(summons, SummonsState.FAILED, "the ushabti is halted")
            try:
                completion = await self._provider.complete(
                    system=system, messages=messages, implements=specs
                )
            except ModelError as error:
                return self._finish(summons, SummonsState.FAILED, f"the model call failed: {error}")
            self._record(
                PapyrusEvent.PROPOSAL,
                {
                    "summons": summons.id,
                    "step": step,
                    "content": completion.content,
                    "stop_reason": completion.stop_reason.value,
                    "calls": [call.model_dump(mode="json") for call in completion.tool_calls],
                },
            )
            if completion.stop_reason is StopReason.REFUSAL:
                return self._finish(summons, SummonsState.FAILED, "the model declined to answer")
            if not completion.tool_calls:
                out_of_steps = False
                break
            messages.append(completion.as_message())
            for call in completion.tool_calls:
                result, reply = await self._handle_call(summons, call)
                tally[result.verdict] += 1
                messages.append(reply)

        counts = ", ".join(f"{count} {verdict.value}" for verdict, count in tally.items() if count)
        detail = f"verdicts: {counts}" if counts else "no implement calls were proposed"
        if out_of_steps:
            detail += f"; stopped after {steps} steps"
        state = SummonsState.ESCALATED if tally[Verdict.NEEDS_APPROVAL] else SummonsState.ANSWERED
        return self._finish(summons, state, detail)

    async def _handle_call(
        self, summons: Summons, call: ToolCall
    ) -> tuple[WeighingResult, Message]:
        """Weigh one proposed call, then run it, record it, or refuse it."""
        kind = self._registry.kind_of(call.name)
        weighed = self._weighing.weigh(
            WeighingRequest(
                implement=call.name,
                kind=kind,
                spell=self.spell,
                halted=self._halted,
                actions_in_last_hour=self._writes_in_last_hour(),
                arguments=call.arguments,
            )
        )
        about: dict[str, JsonValue] = {
            "summons": summons.id,
            "call": call.id,
            "implement": call.name,
            "kind": None if kind is None else kind.value,
        }
        # The verdict is written before anything runs, so the log never trails an action.
        self._record(
            PapyrusEvent.VERDICT,
            {**about, "arguments": call.arguments, "rule": weighed.rule, "reason": weighed.reason},
            weighed.verdict,
        )
        self.metrics.verdicts.labels(verdict=weighed.verdict.value).inc()

        if weighed.verdict is Verdict.DRY_RUN:
            return weighed, Message.tool_result(
                call, f"Dry run: the call was recorded and not run. {weighed.reason}"
            )
        if weighed.verdict is Verdict.NEEDS_APPROVAL:
            return weighed, Message.tool_result(
                call,
                f"Not run: escalated to an operator for approval. {weighed.reason}",
                is_error=True,
            )
        if weighed.verdict is Verdict.REJECT:
            return weighed, Message.tool_result(call, f"Rejected: {weighed.reason}", is_error=True)

        if kind is ImplementKind.WRITE:
            # Counted when the attempt starts, so a failing write still uses the budget.
            self._writes.append(self._clock())
        try:
            implement = self._registry.get(call.name)
            data = await implement.run(
                call.arguments, field=self.spell.field_named(summons.field), weighed=weighed
            )
        except Exception as error:
            reason = str(error) or type(error).__name__
            self._record(PapyrusEvent.ACTION, {**about, "ok": False, "error": reason}, Verdict.PASS)
            reply = Message.tool_result(call, f"Failed: {reason}", is_error=True)
        else:
            self._record(PapyrusEvent.ACTION, {**about, "ok": True, "result": data}, Verdict.PASS)
            reply = Message.tool_result(call, json.dumps(data))
        if kind is not None:
            self.metrics.actions.labels(kind=kind.value).inc()
        return weighed, reply

    def _finish(self, summons: Summons, state: SummonsState, detail: str) -> Summons:
        finished = self._remember(summons.moved_to(state, detail))
        self._record(PapyrusEvent.SUMMONS, finished.model_dump(mode="json"))
        return finished

    def _remember(self, summons: Summons) -> Summons:
        self._summonses[summons.id] = summons
        return summons

    # Prompts

    def _implement_specs(self) -> list[ImplementSpec]:
        return [
            implement.spec()
            for name in self.spell.spec.implements
            if (implement := self._registry.find(name)) is not None
        ]

    def _system_prompt(self) -> str:
        spec = self.spell.spec
        lines = [
            f"You are {self.name}, an ushabti: an agent that tends infrastructure.",
            f"Your role: {spec.role}",
        ]
        if spec.instructions.strip():
            lines += ["", spec.instructions.strip()]
        if spec.maat:
            lines += ["", "Ma'at, the desired state you keep:"]
            lines += [f"- {assertion.id}: {assertion.description}" for assertion in spec.maat]
        if spec.fields:
            lines += ["", "The fields you tend:"]
            lines += [f"- {field.name} ({field.kind.value})" for field in spec.fields]
        lines += [
            "",
            "Use only the implements you are given. Every call passes through a safety "
            "gate called the weighing. A call may be run, recorded as a dry run, "
            "escalated to an operator, or rejected. Do not retry a call that was not "
            "run. When you have nothing more to do, say what you found and what you "
            "would do next.",
        ]
        return "\n".join(lines)

    def _summons_prompt(self, summons: Summons) -> str:
        lines = [f"You are summoned by the {summons.source.value}.", summons.reason]
        if summons.field:
            lines.append(f"Field: {summons.field}")
        if summons.implement:
            lines.append(f"Implement expected to be needed: {summons.implement}")
        return "\n".join(lines)

    # Papyrus

    def _record(
        self,
        event: PapyrusEvent,
        payload: dict[str, JsonValue],
        verdict: Verdict | None = None,
    ) -> None:
        self._papyrus.append(
            PapyrusEntry(
                timestamp=self._clock(),
                ushabti=self.name,
                event=event,
                payload=payload,
                verdict=verdict,
            )
        )
        fields = feed_fields(event, payload, verdict)
        if fields is not None:
            worrying = event is PapyrusEvent.ISFET or (
                event is PapyrusEvent.SUMMONS and payload.get("state") in _FEED_WARNINGS
            )
            log = _log.warning if worrying else _log.info
            log(event.value, ushabti=self.name, **fields)

    def _writes_in_last_hour(self) -> int:
        cutoff = self._clock() - _HOUR
        while self._writes and self._writes[0] <= cutoff:
            self._writes.popleft()
        return len(self._writes)

    def _restore(self) -> None:
        """Rebuild state from Papyrus, so a restart forgets neither a halt nor the hourly count."""
        cutoff = self._clock() - _HOUR
        for entry in self._papyrus.entries():
            if entry.ushabti != self.name:
                continue
            if entry.event is PapyrusEvent.HALT:
                self._halted = True
            elif entry.event is PapyrusEvent.RESUME:
                self._halted = False
            elif (
                entry.event is PapyrusEvent.ACTION
                and entry.payload.get("kind") == ImplementKind.WRITE.value
                and entry.timestamp > cutoff
            ):
                self._writes.append(entry.timestamp)
        self.metrics.halted.set(1 if self._halted else 0)
