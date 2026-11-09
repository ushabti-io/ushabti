"""Routing a summons by field and implement."""

from datetime import timedelta

import pytest

from overseer_testkit import AT, here_am_i, summons
from ushabti import HereAmI
from ushabti_overseer.registry import InMemoryWorkforceRegistry, WorkforceMember
from ushabti_overseer.routing import NoRouteError, Router

K3S = here_am_i("k3s-watcher")
PROXMOX = here_am_i(
    "proxmox-watcher",
    fields=("homelab-proxmox",),
    implements=("proxmox.node_status", "proxmox.start_vm"),
)


def members(*messages: HereAmI, late: timedelta = timedelta(0)) -> tuple[WorkforceMember, ...]:
    registry = InMemoryWorkforceRegistry()
    for message in messages:
        registry.record(message, AT)
    return registry.members(AT + late)


def names(found: list[WorkforceMember]) -> list[str]:
    return [member.name for member in found]


def reason(*args: object, **kwargs: object) -> str:
    with pytest.raises(NoRouteError) as caught:
        Router().candidates(*args, **kwargs)  # type: ignore[arg-type]
    return caught.value.reason


# Success


def test_a_summons_goes_to_the_ushabti_whose_spell_covers_field_and_implement() -> None:
    workforce = members(K3S, PROXMOX)
    router = Router()
    assert names(router.candidates(summons(), workforce)) == ["k3s-watcher"]
    for_proxmox = summons("homelab-proxmox", "proxmox.start_vm")
    assert names(router.candidates(for_proxmox, workforce)) == ["proxmox-watcher"]


def test_a_summons_may_name_only_a_field_or_only_an_implement() -> None:
    workforce = members(K3S, PROXMOX)
    router = Router()
    assert names(router.candidates(summons("homelab-proxmox", None), workforce)) == [
        "proxmox-watcher"
    ]
    assert names(router.candidates(summons(None, "kubernetes.list_pods"), workforce)) == [
        "k3s-watcher"
    ]


def test_a_summons_that_names_neither_can_go_to_anyone() -> None:
    assert names(Router().candidates(summons(None, None), members(K3S, PROXMOX))) == [
        "k3s-watcher",
        "proxmox-watcher",
    ]


def test_work_spreads_to_whoever_was_routed_to_least_recently() -> None:
    workforce = members(here_am_i("a"), here_am_i("b"), here_am_i("c"))
    by_name = {member.name: member for member in workforce}
    router = Router()

    assert names(router.candidates(summons(), workforce)) == ["a", "b", "c"]
    router.delivered(by_name["a"])
    assert names(router.candidates(summons(), workforce)) == ["b", "c", "a"]
    router.delivered(by_name["b"])
    router.delivered(by_name["c"])
    assert names(router.candidates(summons(), workforce)) == ["a", "b", "c"]
    router.delivered(by_name["b"])
    assert names(router.candidates(summons(), workforce)) == ["a", "c", "b"]


def test_unavailable_ushabti_are_skipped_when_another_can_take_it() -> None:
    workforce = members(
        here_am_i("down", halted=True), here_am_i("nowhere", url=""), here_am_i("up")
    )
    assert names(Router().candidates(summons(), workforce)) == ["up"]


# Failure


def test_nobody_registered() -> None:
    assert reason(summons(), ()) == "no ushabti is registered"


def test_no_ushabti_tends_the_field() -> None:
    assert reason(summons("elsewhere", None), members(K3S)) == (
        "no ushabti tends the field elsewhere"
    )


def test_no_ushabti_may_use_the_implement() -> None:
    assert reason(summons(None, "proxmox.start_vm"), members(K3S)) == (
        "no ushabti may use the implement proxmox.start_vm"
    )


def test_field_and_implement_must_be_covered_by_the_same_ushabti() -> None:
    # One tends the field and the other holds the implement. Neither covers both.
    assert reason(summons("homelab-k3s", "proxmox.start_vm"), members(K3S, PROXMOX)) == (
        "no ushabti tends the field homelab-k3s with the implement proxmox.start_vm"
    )


def test_every_candidate_lost_or_halted() -> None:
    lost = members(here_am_i("a"), late=timedelta(hours=1))
    assert reason(summons(), lost) == "every ushabti that covers it is unavailable: a is lost"

    mixed = members(here_am_i("a", halted=True), here_am_i("b", halted=True))
    assert reason(summons(), mixed) == (
        "every ushabti that covers it is unavailable: a is halted, b is halted"
    )


def test_a_candidate_without_a_url_cannot_be_reached() -> None:
    assert reason(summons(), members(here_am_i("a", url=""))) == (
        "no ushabti that covers it advertises a URL: a"
    )
