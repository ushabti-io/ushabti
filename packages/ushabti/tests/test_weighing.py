"""DefaultWeighing: every rule, and the order the rules are applied in."""

from typing import TypedDict, Unpack

import pytest

from sdk_testkit import make_spell
from ushabti import (
    DefaultWeighing,
    ImplementKind,
    Verdict,
    WeighingPolicy,
    WeighingRequest,
    WeighingRule,
)

READ = "noop.read"
WRITE = "noop.write"


class Policy(TypedDict, total=False):
    dry_run: bool
    require_approval_for: list[str]
    max_actions_per_hour: int


LOOSE: Policy = {"dry_run": False, "require_approval_for": [], "max_actions_per_hour": 5}


def weigh(
    implement: str = WRITE,
    kind: ImplementKind | None = ImplementKind.WRITE,
    *,
    halted: bool = False,
    actions: int = 0,
    **policy: Unpack[Policy],
) -> tuple[Verdict, str]:
    spell = make_spell(weighing=policy) if policy else make_spell()
    result = DefaultWeighing().weigh(
        WeighingRequest(
            implement=implement,
            kind=kind,
            spell=spell,
            halted=halted,
            actions_in_last_hour=actions,
        )
    )
    assert result.implement == implement
    assert result.reason, "every verdict carries a human-readable reason"
    return result.verdict, result.rule


def test_policy_defaults_are_the_safe_ones() -> None:
    policy = WeighingPolicy()
    assert policy.dry_run is True
    assert policy.require_approval_for == ("write",)
    assert policy.max_actions_per_hour == 5


def test_rule_1_halted_rejects() -> None:
    assert weigh(halted=True, **LOOSE) == (Verdict.REJECT, WeighingRule.HALTED)


def test_rule_2_unlisted_implement_rejects() -> None:
    verdict = weigh("kubernetes.scale_deployment", ImplementKind.WRITE, **LOOSE)
    assert verdict == (Verdict.REJECT, WeighingRule.NOT_LISTED)


def test_rule_2_unregistered_name_rejects_even_when_listed() -> None:
    assert weigh(WRITE, None, **LOOSE) == (Verdict.REJECT, WeighingRule.NOT_LISTED)


def test_rule_3_read_passes() -> None:
    assert weigh(READ, ImplementKind.READ) == (Verdict.PASS, WeighingRule.READ)


def test_rule_4_dry_run_records_instead_of_running() -> None:
    verdict = weigh(dry_run=True, require_approval_for=[], max_actions_per_hour=5)
    assert verdict == (Verdict.DRY_RUN, WeighingRule.DRY_RUN)


def test_rule_4_is_the_default_for_a_spell_with_no_weighing_block() -> None:
    assert weigh() == (Verdict.DRY_RUN, WeighingRule.DRY_RUN)


@pytest.mark.parametrize("entry", ["write", WRITE])
def test_rule_5_approval_by_kind_or_by_name(entry: str) -> None:
    verdict = weigh(dry_run=False, require_approval_for=[entry], max_actions_per_hour=5)
    assert verdict == (Verdict.NEEDS_APPROVAL, WeighingRule.APPROVAL_REQUIRED)


def test_rule_5_does_not_match_another_implements_name() -> None:
    spell_implements = [READ, WRITE, "kubernetes.restart_deployment"]
    spell = make_spell(
        implements=spell_implements,
        weighing={
            "dry_run": False,
            "require_approval_for": ["kubernetes.restart_deployment"],
            "max_actions_per_hour": 5,
        },
    )
    result = DefaultWeighing().weigh(
        WeighingRequest(implement=WRITE, kind=ImplementKind.WRITE, spell=spell, halted=False)
    )
    assert result.verdict is Verdict.PASS


@pytest.mark.parametrize(
    ("actions", "limit", "expected"),
    [
        (0, 5, Verdict.PASS),
        (4, 5, Verdict.PASS),
        (5, 5, Verdict.REJECT),
        (9, 5, Verdict.REJECT),
        (0, 0, Verdict.REJECT),
        (0, 1, Verdict.PASS),
        (1, 1, Verdict.REJECT),
    ],
)
def test_rule_6_hourly_limit(actions: int, limit: int, expected: Verdict) -> None:
    verdict, rule = weigh(
        actions=actions, dry_run=False, require_approval_for=[], max_actions_per_hour=limit
    )
    assert verdict is expected
    assert rule == (WeighingRule.ALLOWED if expected is Verdict.PASS else WeighingRule.RATE_LIMITED)


def test_rule_7_otherwise_passes() -> None:
    assert weigh(**LOOSE) == (Verdict.PASS, WeighingRule.ALLOWED)


# The order of the rules. Each case would match several rules, and the earliest must win.


def test_order_halted_beats_everything() -> None:
    # Unlisted, a read, over the limit, and halted: rule 1 decides.
    for implement, kind in (
        ("kubernetes.scale_deployment", ImplementKind.WRITE),
        (READ, ImplementKind.READ),
        (WRITE, ImplementKind.WRITE),
    ):
        verdict = weigh(implement, kind, halted=True, actions=99, **LOOSE)
        assert verdict == (Verdict.REJECT, WeighingRule.HALTED)


def test_order_halted_rejects_reads_too() -> None:
    assert weigh(READ, ImplementKind.READ, halted=True) == (Verdict.REJECT, WeighingRule.HALTED)


def test_order_not_listed_beats_read() -> None:
    verdict = weigh("kubernetes.list_pods", ImplementKind.READ)
    assert verdict == (Verdict.REJECT, WeighingRule.NOT_LISTED)


def test_order_read_beats_dry_run_approval_and_limit() -> None:
    verdict = weigh(
        READ,
        ImplementKind.READ,
        actions=99,
        dry_run=True,
        require_approval_for=["read", "write"],
        max_actions_per_hour=0,
    )
    assert verdict == (Verdict.PASS, WeighingRule.READ)


def test_order_dry_run_beats_approval_and_limit() -> None:
    verdict = weigh(
        actions=99, dry_run=True, require_approval_for=["write"], max_actions_per_hour=0
    )
    assert verdict == (Verdict.DRY_RUN, WeighingRule.DRY_RUN)


def test_order_approval_beats_limit() -> None:
    verdict = weigh(
        actions=99, dry_run=False, require_approval_for=["write"], max_actions_per_hour=0
    )
    assert verdict == (Verdict.NEEDS_APPROVAL, WeighingRule.APPROVAL_REQUIRED)


def test_the_rules_are_declared_in_application_order() -> None:
    assert [rule.value for rule in WeighingRule] == [
        "halted",
        "not_listed",
        "read",
        "dry_run",
        "approval_required",
        "rate_limited",
        "allowed",
    ]


def test_verdict_values_match_the_glossary() -> None:
    assert [verdict.value for verdict in Verdict] == [
        "PASS",
        "DRY_RUN",
        "NEEDS_APPROVAL",
        "REJECT",
    ]
