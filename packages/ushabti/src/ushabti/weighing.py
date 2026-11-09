"""The weighing: the safety gate every write implement must pass."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import JsonValue, NonNegativeInt

from ushabti._base import StrictModel, WireModel
from ushabti.implements import ImplementKind

if TYPE_CHECKING:
    from ushabti.spell import Spell


class Verdict(StrEnum):
    """The outcome of a weighing."""

    PASS = "PASS"  # noqa: S105  (a verdict name, not a password)
    DRY_RUN = "DRY_RUN"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"
    REJECT = "REJECT"


class WeighingRule(StrEnum):
    """The rules of the default weighing, in the order they are applied."""

    HALTED = "halted"
    NOT_LISTED = "not_listed"
    READ = "read"
    DRY_RUN = "dry_run"
    APPROVAL_REQUIRED = "approval_required"
    RATE_LIMITED = "rate_limited"
    ALLOWED = "allowed"


class WeighingPolicy(StrictModel):
    """How strictly the weighing treats write implements."""

    dry_run: bool = True
    """When true, write implements are recorded and never run. This is the default."""

    require_approval_for: tuple[str, ...] = ("write",)
    """Implement names or kinds that need operator approval. `write` covers every
    write implement."""

    max_actions_per_hour: NonNegativeInt = 5
    """How many write implements may run in any one hour. Further writes are rejected."""


@dataclass(frozen=True, slots=True)
class WeighingRequest:
    """Everything a weighing may consider about one proposed implement call."""

    implement: str
    """Name of the implement the model proposed."""

    kind: ImplementKind | None
    """Kind of the implement, or None when the name is not registered."""

    spell: "Spell"
    """The spell of the ushabti that would run it."""

    halted: bool
    """Whether the ushabti is halted."""

    actions_in_last_hour: int = 0
    """How many write implements ran in the last hour."""

    arguments: Mapping[str, JsonValue] = dataclass_field(default_factory=dict)
    """The proposed arguments."""


class WeighingResult(WireModel):
    """A verdict with the reason for it."""

    implement: str
    """Name of the implement that was weighed."""

    verdict: Verdict
    """The verdict: `PASS`, `DRY_RUN`, `NEEDS_APPROVAL`, or `REJECT`."""

    rule: str
    """Which rule decided the verdict."""

    reason: str
    """Why, in plain language."""


class Weighing(Protocol):
    """The safety gate. Anything with this method can replace the default."""

    def weigh(self, request: WeighingRequest) -> WeighingResult: ...


class DefaultWeighing:
    """The default safety gate. Rules are applied in order; the first match decides.

    1. The ushabti is halted: REJECT.
    2. The implement is not listed in the spell: REJECT.
    3. The implement is read: PASS.
    4. The spell has dry_run true: DRY_RUN.
    5. The implement or its kind is listed in require_approval_for: NEEDS_APPROVAL.
    6. The write would exceed max_actions_per_hour: REJECT.
    7. Otherwise: PASS.
    """

    def weigh(self, request: WeighingRequest) -> WeighingResult:
        name = request.implement
        policy = request.spell.spec.weighing

        def result(verdict: Verdict, rule: WeighingRule, reason: str) -> WeighingResult:
            return WeighingResult(implement=name, verdict=verdict, rule=rule.value, reason=reason)

        if request.halted:
            return result(
                Verdict.REJECT,
                WeighingRule.HALTED,
                "the ushabti is halted, so it does not act",
            )
        if name not in request.spell.spec.implements:
            return result(
                Verdict.REJECT,
                WeighingRule.NOT_LISTED,
                f"{name} is not listed in the spell's implements",
            )
        if request.kind is None:
            return result(
                Verdict.REJECT,
                WeighingRule.NOT_LISTED,
                f"{name} is listed in the spell but is not a registered implement",
            )
        if request.kind is ImplementKind.READ:
            return result(Verdict.PASS, WeighingRule.READ, f"{name} is a read implement")
        if policy.dry_run:
            return result(
                Verdict.DRY_RUN,
                WeighingRule.DRY_RUN,
                f"the spell has dry_run set, so {name} is recorded and not run",
            )
        if name in policy.require_approval_for or request.kind.value in policy.require_approval_for:
            return result(
                Verdict.NEEDS_APPROVAL,
                WeighingRule.APPROVAL_REQUIRED,
                f"{name} needs operator approval under require_approval_for",
            )
        if request.actions_in_last_hour + 1 > policy.max_actions_per_hour:
            return result(
                Verdict.REJECT,
                WeighingRule.RATE_LIMITED,
                f"{name} would exceed max_actions_per_hour "
                f"({request.actions_in_last_hour} of {policy.max_actions_per_hour} already used)",
            )
        return result(
            Verdict.PASS,
            WeighingRule.ALLOWED,
            f"{name} is listed, approved by policy, and within the hourly limit",
        )
