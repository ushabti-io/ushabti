"""Registration, "Here am I" tracking, and loss detection."""

from datetime import timedelta

import pytest

from overseer_testkit import AT, here_am_i
from ushabti_overseer.registry import (
    InMemoryWorkforceRegistry,
    UshabtiStatus,
    WorkforceRegistry,
)


def registry(**kwargs: float) -> InMemoryWorkforceRegistry:
    return InMemoryWorkforceRegistry(here_am_i_interval=30.0, **kwargs)  # type: ignore[arg-type]


def test_the_first_here_am_i_registers_an_ushabti() -> None:
    workforce = registry()
    member = workforce.record(here_am_i("k3s-watcher"), AT)

    assert member.name == "k3s-watcher"
    assert member.spell == "k3s-watcher-spell"
    assert member.decade == 1
    assert member.status is UshabtiStatus.ACTIVE
    assert member.last_here_am_i == AT
    assert member.fields == ("homelab-k3s",)
    assert member.implements == ("kubernetes.list_pods", "kubernetes.restart_deployment")
    assert member.url == "http://k3s-watcher.example.invalid:8080"
    assert workforce.members(AT) == (member,)
    assert workforce.get("k3s-watcher", AT) == member
    assert workforce.get("stranger", AT) is None


def test_a_later_here_am_i_refreshes_and_does_not_duplicate() -> None:
    workforce = registry()
    workforce.record(here_am_i("a"), AT)
    later = AT + timedelta(seconds=30)
    member = workforce.record(here_am_i("a", fields=("other-field",)), later)

    assert len(workforce.members(later)) == 1
    assert member.last_here_am_i == later
    assert member.fields == ("other-field",), "the newest message wins"
    assert member.decade == 1


def test_members_are_listed_by_name_with_their_decades() -> None:
    workforce = registry(decade_size=2)
    for name in ("c", "a", "b"):
        workforce.record(here_am_i(name), AT)
    members = workforce.members(AT)
    assert [member.name for member in members] == ["a", "b", "c"]
    assert {member.name: member.decade for member in members} == {"c": 1, "a": 1, "b": 2}
    assert [decade.members for decade in workforce.decades()] == [("c", "a"), ("b",)]


def test_an_ushabti_is_lost_after_three_missed_messages_by_default() -> None:
    workforce = registry()
    workforce.record(here_am_i("a"), AT)

    def status(seconds: float) -> UshabtiStatus:
        member = workforce.get("a", AT + timedelta(seconds=seconds))
        assert member is not None
        return member.status

    assert status(0) is UshabtiStatus.ACTIVE
    assert status(31) is UshabtiStatus.ACTIVE, "one missed"
    assert status(61) is UshabtiStatus.ACTIVE, "two missed"
    assert status(90) is UshabtiStatus.ACTIVE, "the third is only now due"
    assert status(90.001) is UshabtiStatus.LOST, "three missed"
    assert status(3600) is UshabtiStatus.LOST


@pytest.mark.parametrize(("missed_limit", "lost_after"), [(1, 30), (2, 60), (5, 150)])
def test_the_missed_limit_is_configurable(missed_limit: int, lost_after: float) -> None:
    workforce = registry(missed_limit=missed_limit)
    workforce.record(here_am_i("a"), AT)
    at_limit = workforce.get("a", AT + timedelta(seconds=lost_after))
    beyond = workforce.get("a", AT + timedelta(seconds=lost_after + 1))
    assert at_limit is not None
    assert beyond is not None
    assert at_limit.status is UshabtiStatus.ACTIVE
    assert beyond.status is UshabtiStatus.LOST


def test_a_lost_ushabti_keeps_its_place_and_returns_when_it_answers() -> None:
    workforce = registry()
    workforce.record(here_am_i("a"), AT)
    much_later = AT + timedelta(hours=1)
    (lost,) = workforce.members(much_later)
    assert lost.status is UshabtiStatus.LOST
    assert lost.last_here_am_i == AT
    assert lost.decade == 1

    back = workforce.record(here_am_i("a"), much_later)
    assert back.status is UshabtiStatus.ACTIVE
    assert back.decade == 1


def test_only_the_silent_ushabti_is_lost() -> None:
    workforce = registry()
    workforce.record(here_am_i("quiet"), AT)
    workforce.record(here_am_i("chatty"), AT)
    later = AT + timedelta(seconds=120)
    workforce.record(here_am_i("chatty"), later)
    statuses = {member.name: member.status for member in workforce.members(later)}
    assert statuses == {"quiet": UshabtiStatus.LOST, "chatty": UshabtiStatus.ACTIVE}


def test_a_halted_ushabti_is_reported_as_halted() -> None:
    workforce = registry()
    member = workforce.record(here_am_i("a", halted=True), AT)
    assert member.status is UshabtiStatus.HALTED
    lost = workforce.get("a", AT + timedelta(hours=1))
    assert lost is not None
    assert lost.status is UshabtiStatus.LOST, "silence outranks the last known state"


def test_covers() -> None:
    member = registry().record(here_am_i("a"), AT)
    assert member.covers("homelab-k3s", "kubernetes.list_pods")
    assert member.covers("homelab-k3s", None)
    assert member.covers(None, "kubernetes.list_pods")
    assert member.covers(None, None)
    assert not member.covers("elsewhere", "kubernetes.list_pods")
    assert not member.covers("homelab-k3s", "proxmox.start_vm")


def test_settings_are_validated() -> None:
    with pytest.raises(ValueError, match="here_am_i_interval must be greater than zero"):
        InMemoryWorkforceRegistry(here_am_i_interval=0)
    with pytest.raises(ValueError, match="missed_limit must be at least 1"):
        InMemoryWorkforceRegistry(missed_limit=0)


def test_the_in_memory_registry_satisfies_the_protocol() -> None:
    workforce: WorkforceRegistry = InMemoryWorkforceRegistry()
    workforce.record(here_am_i("a"), AT)
    assert len(workforce.members(AT)) == 1
    assert len(workforce.decades()) == 1
