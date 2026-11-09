"""Ma'at evaluation: in balance, isfet, omen, or unknown."""

from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest
from pydantic import JsonValue, ValidationError

from ushabti import Comparison, Condition, Maat, MaatAssertion, MaatStatus

AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def assertion(**overrides: Any) -> MaatAssertion:
    data: dict[str, Any] = {
        "id": "disk-has-room",
        "description": "The root disk stays below 90 percent.",
        "observe": {"implement": "noop.read"},
        "expect": {"path": "used_percent", "op": "lt", "value": 90},
        "omen": {"path": "used_percent", "op": "ge", "value": 80},
    }
    data.update(overrides)
    return MaatAssertion.model_validate(data)


def evaluate(observation: JsonValue, **overrides: Any) -> Any:
    item = assertion(**overrides)
    numbers = count(1)
    return Maat([item]).evaluate(
        item, observation, field="root-disk", at=AT, ids=lambda: f"id-{next(numbers)}"
    )


def test_in_balance_when_expect_holds_and_no_warning() -> None:
    outcome = evaluate({"used_percent": 40})
    assert outcome.status is MaatStatus.IN_BALANCE
    assert outcome.isfet is None
    assert outcome.omen is None
    assert outcome.reason == "holds: used_percent lt 90"


def test_isfet_when_expect_does_not_hold() -> None:
    outcome = evaluate({"used_percent": 95})
    assert outcome.status is MaatStatus.ISFET
    assert outcome.omen is None
    isfet = outcome.isfet
    assert isfet is not None
    assert isfet.model_dump() == {
        "id": "id-1",
        "assertion": "disk-has-room",
        "field": "root-disk",
        "description": "The root disk stays below 90 percent.",
        "observed": 95,
        "expected": "used_percent lt 90",
        "detected_at": AT,
    }


def test_omen_when_expect_holds_but_the_warning_is_met() -> None:
    outcome = evaluate({"used_percent": 85})
    assert outcome.status is MaatStatus.OMEN
    assert outcome.isfet is None
    omen = outcome.omen
    assert omen is not None
    assert omen.model_dump() == {
        "id": "id-1",
        "assertion": "disk-has-room",
        "field": "root-disk",
        "description": "The root disk stays below 90 percent.",
        "observed": 85,
        "warning": "used_percent ge 80",
        "predicted_at": AT,
    }


def test_isfet_wins_over_an_omen() -> None:
    assert evaluate({"used_percent": 99}).status is MaatStatus.ISFET


def test_unknown_when_the_path_is_absent() -> None:
    outcome = evaluate({"other": 1})
    assert outcome.status is MaatStatus.UNKNOWN
    assert outcome.reason == "could not evaluate: used_percent lt 90"


def test_unknown_when_values_cannot_be_ordered() -> None:
    assert evaluate({"used_percent": "plenty"}).status is MaatStatus.UNKNOWN
    assert evaluate({"used_percent": True}).status is MaatStatus.UNKNOWN
    assert evaluate({"used_percent": None}).status is MaatStatus.UNKNOWN


def test_unknown_without_an_expect_condition() -> None:
    item = MaatAssertion(id="prose-only", description="Things are fine.")
    outcome = Maat([item]).evaluate(item, {"anything": 1})
    assert outcome.status is MaatStatus.UNKNOWN
    assert "no expect condition" in outcome.reason


def test_an_unevaluable_omen_does_not_disturb_the_balance() -> None:
    outcome = evaluate({"used_percent": 10}, omen={"path": "absent", "op": "ge", "value": 80})
    assert outcome.status is MaatStatus.IN_BALANCE


def test_defaults_fill_in_time_and_ids() -> None:
    item = assertion()
    outcome = Maat([item]).evaluate(item, {"used_percent": 95})
    assert outcome.isfet is not None
    assert outcome.isfet.id
    assert outcome.isfet.field is None
    assert outcome.isfet.detected_at.tzinfo is not None


@pytest.mark.parametrize(
    ("op", "observed", "value", "expected"),
    [
        ("eq", 3, 3, True),
        ("eq", 3, 3.0, True),
        ("eq", 3, 4, False),
        ("eq", "Running", "Running", True),
        ("eq", True, True, True),
        ("eq", None, None, True),
        ("ne", 3, 4, True),
        ("ne", "a", "a", False),
        ("lt", 1, 2, True),
        ("lt", 2, 2, False),
        ("le", 2, 2, True),
        ("le", 3, 2, False),
        ("gt", 3, 2, True),
        ("gt", 2, 2, False),
        ("ge", 2, 2, True),
        ("ge", 1, 2, False),
        ("lt", "a", "b", True),
        ("ge", 2.5, 2, True),
        ("lt", "a", 1, None),
        ("gt", [1], 1, None),
    ],
)
def test_comparisons(op: str, observed: JsonValue, value: Any, expected: bool | None) -> None:
    condition = Condition(op=Comparison(op), value=value)
    assert condition.evaluate(observed) == (expected, observed)


def test_a_flag_never_equals_a_count() -> None:
    # Python says True == 1. A health check must not.
    assert Condition(op=Comparison.EQ, value=True).evaluate(1)[0] is False
    assert Condition(op=Comparison.EQ, value=1).evaluate(True)[0] is False
    assert Condition(op=Comparison.NE, value=0).evaluate(False)[0] is True


def test_paths_walk_mappings_and_lists() -> None:
    data: JsonValue = {
        "status": {"ready": True},
        "items": [{"phase": "Running"}, {"phase": "Failed"}],
    }
    assert Condition(path="status.ready", op=Comparison.EQ, value=True).evaluate(data)[0] is True
    assert Condition(path="items.1.phase", op=Comparison.EQ, value="Failed").evaluate(data)[0]
    assert Condition(path="items.2.phase", op=Comparison.EQ, value="x").evaluate(data)[0] is None
    assert Condition(path="items.one", op=Comparison.EQ, value="x").evaluate(data)[0] is None
    assert (
        Condition(path="status.ready.deeper", op=Comparison.EQ, value=1).evaluate(data)[0] is None
    )


def test_an_empty_path_means_the_whole_observation() -> None:
    condition = Condition(op=Comparison.GE, value=3)
    assert condition.evaluate(5) == (True, 5)
    assert condition.describe() == "observation ge 3"


def test_maat_holds_assertions_in_order_and_knows_which_are_checkable() -> None:
    prose = MaatAssertion(id="prose", description="Looks fine.")
    checked = assertion()
    maat = Maat([prose, checked])
    assert list(maat) == [prose, checked]
    assert len(maat) == 2
    assert maat.checkable == (checked,)


def test_duplicate_assertion_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate Ma'at assertion id: disk-has-room"):
        Maat([assertion(), assertion()])


def test_expect_and_omen_need_observe() -> None:
    with pytest.raises(ValidationError, match="expect and omen need an observe block"):
        assertion(observe=None)
    with pytest.raises(ValidationError, match="observe needs an expect condition"):
        assertion(expect=None, omen=None)
