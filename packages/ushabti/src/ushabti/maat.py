"""Ma'at (the desired state) and isfet (current drift from it)."""

import json
from collections.abc import Callable, Iterable, Iterator, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Final, Self

import pydantic
from pydantic import JsonValue, model_validator

from ushabti._base import Name, StrictModel, WireModel, new_id, utc_now
from ushabti.omen import Omen

JsonScalar = str | int | float | bool | None

_MISSING: Final = object()


class Comparison(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"


class Condition(StrictModel):
    """One comparison against an observation."""

    path: str = ""
    """Dotted path into the observation, such as `status.ready` or `items.0.phase`.
    Empty means the whole observation."""

    op: Comparison
    """Comparison to apply: `eq`, `ne`, `lt`, `le`, `gt`, or `ge`."""

    value: JsonScalar
    """Value to compare the observation against."""

    def describe(self) -> str:
        """The condition in readable form, such as ``used_percent lt 90``."""
        return f"{self.path or 'observation'} {self.op.value} {json.dumps(self.value)}"

    def evaluate(self, observation: JsonValue) -> tuple[bool | None, object]:
        """Apply the condition.

        Returns the outcome and the observed value. The outcome is None when the
        condition cannot be decided: the path is absent, or the two values
        cannot be ordered.
        """
        observed = _lookup(observation, self.path)
        if observed is _MISSING:
            return None, None
        return _compare(self.op, observed, self.value), observed


class Observe(StrictModel):
    """Which read implement the vigil calls to check an assertion."""

    implement: str
    """Name of a read implement listed in the spell."""

    arguments: dict[str, JsonValue] = pydantic.Field(default_factory=dict)
    """Arguments passed to the implement."""


class MaatAssertion(StrictModel):
    """One statement about what healthy looks like."""

    id: Name
    """Identifier of the assertion, unique within the spell."""

    description: str
    """What healthy looks like, in plain language. The model reads this."""

    field: str | None = None
    """Name of the field the assertion is about. Optional when the spell has one field."""

    observe: Observe | None = None
    """The observation the vigil makes each tick. Without it the assertion is
    context for the model only and is never evaluated."""

    expect: Condition | None = None
    """Condition that must hold. When it does not, the vigil raises isfet (drift:
    something is wrong now)."""

    omen: Condition | None = None
    """Early-warning condition. When it holds while `expect` still holds, the
    vigil raises an omen (a prediction that something will be wrong soon)."""

    @model_validator(mode="after")
    def _check_is_complete(self) -> Self:
        if self.observe is None and (self.expect is not None or self.omen is not None):
            raise ValueError("expect and omen need an observe block")
        if self.observe is not None and self.expect is None:
            raise ValueError("observe needs an expect condition")
        return self


class MaatStatus(StrEnum):
    IN_BALANCE = "in_balance"
    ISFET = "isfet"
    OMEN = "omen"
    UNKNOWN = "unknown"


class Isfet(WireModel):
    """Current drift from Ma'at: something is wrong now."""

    id: str
    """Identifier of this drift report."""

    assertion: str
    """Id of the Ma'at assertion that does not hold."""

    field: str | None = None
    """Name of the field the drift concerns, when known."""

    description: str
    """What the assertion says healthy looks like."""

    observed: JsonValue = None
    """The observed value that broke the assertion."""

    expected: str
    """The condition that should have held, in readable form."""

    detected_at: datetime
    """When the drift was detected."""


class MaatOutcome(WireModel):
    """The result of comparing one observation to one assertion."""

    assertion: str
    status: MaatStatus
    reason: str
    isfet: Isfet | None = None
    omen: Omen | None = None


class Maat:
    """The desired state: the assertions of one spell."""

    def __init__(self, assertions: Iterable[MaatAssertion]) -> None:
        self._assertions = tuple(assertions)
        seen: set[str] = set()
        for assertion in self._assertions:
            if assertion.id in seen:
                raise ValueError(f"duplicate Ma'at assertion id: {assertion.id}")
            seen.add(assertion.id)

    def __iter__(self) -> Iterator[MaatAssertion]:
        return iter(self._assertions)

    def __len__(self) -> int:
        return len(self._assertions)

    @property
    def checkable(self) -> tuple[MaatAssertion, ...]:
        """Assertions the vigil can evaluate, in spell order."""
        return tuple(a for a in self._assertions if a.observe is not None)

    def evaluate(
        self,
        assertion: MaatAssertion,
        observation: JsonValue,
        *,
        field: str | None = None,
        at: datetime | None = None,
        ids: Callable[[], str] = new_id,
    ) -> MaatOutcome:
        """Compare an observation to an assertion and report isfet or an omen."""
        if assertion.expect is None:
            return MaatOutcome(
                assertion=assertion.id,
                status=MaatStatus.UNKNOWN,
                reason="the assertion has no expect condition, so it cannot be evaluated",
            )
        moment = utc_now() if at is None else at
        holds, observed = assertion.expect.evaluate(observation)
        if holds is None:
            return MaatOutcome(
                assertion=assertion.id,
                status=MaatStatus.UNKNOWN,
                reason=f"could not evaluate: {assertion.expect.describe()}",
            )
        if not holds:
            isfet = Isfet(
                id=ids(),
                assertion=assertion.id,
                field=field,
                description=assertion.description,
                observed=_as_json(observed),
                expected=assertion.expect.describe(),
                detected_at=moment,
            )
            return MaatOutcome(
                assertion=assertion.id,
                status=MaatStatus.ISFET,
                reason=f"expected {isfet.expected}, observed {json.dumps(isfet.observed)}",
                isfet=isfet,
            )
        if assertion.omen is not None:
            warned, seen = assertion.omen.evaluate(observation)
            if warned:
                omen = Omen(
                    id=ids(),
                    assertion=assertion.id,
                    field=field,
                    description=assertion.description,
                    observed=_as_json(seen),
                    warning=assertion.omen.describe(),
                    predicted_at=moment,
                )
                return MaatOutcome(
                    assertion=assertion.id,
                    status=MaatStatus.OMEN,
                    reason=f"warning met: {omen.warning}, observed {json.dumps(omen.observed)}",
                    omen=omen,
                )
        return MaatOutcome(
            assertion=assertion.id,
            status=MaatStatus.IN_BALANCE,
            reason=f"holds: {assertion.expect.describe()}",
        )


def _lookup(data: JsonValue, path: str) -> object:
    current: object = data
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _equal(left: object, right: object) -> bool:
    if _is_number(left) and _is_number(right):
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        # Python treats True as 1. A health flag must never equal a count.
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    return left == right


def _compare(op: Comparison, observed: object, expected: JsonScalar) -> bool | None:
    if op is Comparison.EQ:
        return _equal(observed, expected)
    if op is Comparison.NE:
        return not _equal(observed, expected)
    if isinstance(observed, bool) or isinstance(expected, bool):
        return None
    if isinstance(observed, int | float) and isinstance(expected, int | float):
        return _order(op, float(observed), float(expected))
    if isinstance(observed, str) and isinstance(expected, str):
        return _order(op, observed, expected)
    return None


def _order[T: (float, str)](op: Comparison, left: T, right: T) -> bool:
    if op is Comparison.LT:
        return left < right
    if op is Comparison.LE:
        return left <= right
    if op is Comparison.GT:
        return left > right
    return left >= right


def _as_json(value: object) -> JsonValue:
    """Round-trip through JSON so the value is safe to store in a model."""
    result: JsonValue = json.loads(json.dumps(value, default=str))
    return result
