"""Implements: the verdict guard, the registry, and the scaffold stubs."""

import inspect
from collections.abc import Mapping
from typing import ClassVar

import pytest
from pydantic import JsonValue

from sdk_testkit import weighed
from ushabti import (
    Field,
    FieldKind,
    Implement,
    ImplementKind,
    ImplementRegistry,
    UnknownImplementError,
    UnweighedWriteError,
    Verdict,
    default_registry,
)
from ushabti.implements import builtin_implements
from ushabti.implements.noop import NoopRead, NoopWrite

STUBS = {
    "kubernetes.list_pods": ImplementKind.READ,
    "kubernetes.get_deployment": ImplementKind.READ,
    "kubernetes.restart_deployment": ImplementKind.WRITE,
    "kubernetes.scale_deployment": ImplementKind.WRITE,
    "proxmox.node_status": ImplementKind.READ,
    "proxmox.list_vms": ImplementKind.READ,
    "proxmox.start_vm": ImplementKind.WRITE,
    "prometheus.query": ImplementKind.READ,
    "prometheus.query_range": ImplementKind.READ,
}

WRITES = [i for i in builtin_implements() if i.kind is ImplementKind.WRITE]
READS = [i for i in builtin_implements() if i.kind is ImplementKind.READ]


# No write implement can execute without a verdict.


@pytest.mark.parametrize("implement", WRITES, ids=lambda i: i.name)
async def test_no_write_implement_runs_without_a_verdict(implement: Implement) -> None:
    with pytest.raises(UnweighedWriteError, match="was not weighed"):
        await implement.run({})


@pytest.mark.parametrize("implement", WRITES, ids=lambda i: i.name)
@pytest.mark.parametrize("verdict", [Verdict.DRY_RUN, Verdict.NEEDS_APPROVAL, Verdict.REJECT])
async def test_no_write_implement_runs_on_a_verdict_other_than_pass(
    implement: Implement, verdict: Verdict
) -> None:
    with pytest.raises(UnweighedWriteError, match="only PASS may run"):
        await implement.run({}, weighed=weighed(implement.name, verdict))


@pytest.mark.parametrize("implement", WRITES, ids=lambda i: i.name)
async def test_a_pass_for_another_implement_does_not_count(implement: Implement) -> None:
    with pytest.raises(UnweighedWriteError, match=r"the verdict is for noop\.read"):
        await implement.run({}, weighed=weighed("noop.read"))


async def test_the_guard_stops_the_write_before_it_does_anything() -> None:
    write = NoopWrite()
    for attempt in (None, weighed("noop.write", Verdict.DRY_RUN), weighed("noop.read")):
        with pytest.raises(UnweighedWriteError):
            await write.run({"replicas": 0}, weighed=attempt)
    assert write.calls == []


async def test_a_write_runs_with_a_pass_verdict() -> None:
    write = NoopWrite()
    result = await write.run({"replicas": 3}, weighed=weighed("noop.write"))
    assert result == {"ok": True, "arguments": {"replicas": 3}}
    assert write.calls == [{"replicas": 3}]


def test_there_are_write_implements_to_guard() -> None:
    assert {i.name for i in WRITES} == {
        "noop.write",
        "kubernetes.restart_deployment",
        "kubernetes.scale_deployment",
        "proxmox.start_vm",
    }


async def test_a_read_may_observe_without_a_verdict() -> None:
    assert await NoopRead().run({"healthy": True}) == {"healthy": True}


async def test_a_read_given_a_verdict_must_also_have_passed() -> None:
    read = NoopRead()
    assert await read.run({"a": 1}, weighed=weighed("noop.read")) == {"a": 1}
    with pytest.raises(UnweighedWriteError, match="only PASS may run"):
        await read.run({}, weighed=weighed("noop.read", Verdict.REJECT))


async def test_the_field_reaches_the_implement() -> None:
    seen: list[Field | None] = []

    class Spy(Implement):
        name: ClassVar[str] = "spy.look"
        kind: ClassVar[ImplementKind] = ImplementKind.READ
        description: ClassVar[str] = "Remember the field it was given."

        async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
            seen.append(field)
            return None

    field = Field(name="here", kind=FieldKind.SERVICE)
    await Spy().run({}, field=field)
    assert seen == [field]


# The scaffold stubs


def test_every_stub_operation_is_declared_with_its_kind() -> None:
    registry = default_registry()
    assert {name: registry.get(name).kind for name in STUBS} == STUBS
    assert set(registry.names()) == {*STUBS, "noop.read", "noop.write"}


@pytest.mark.parametrize("name", sorted(STUBS))
async def test_every_stub_raises_not_implemented_and_says_what_is_missing(name: str) -> None:
    implement = default_registry().get(name)
    verdict = weighed(name) if implement.kind is ImplementKind.WRITE else None
    with pytest.raises(NotImplementedError) as caught:
        await implement.run({}, weighed=verdict)
    message = str(caught.value)
    assert name in message
    assert "not implemented yet" in message
    assert "extra" in message


@pytest.mark.parametrize("name", sorted(STUBS))
def test_every_stub_is_marked_todo_scaffold(name: str) -> None:
    source = inspect.getsource(type(default_registry().get(name))._run)
    assert "# TODO(scaffold):" in source


@pytest.mark.parametrize("implement", builtin_implements(), ids=lambda i: i.name)
def test_every_implement_describes_itself_for_a_model(implement: Implement) -> None:
    spec = implement.spec()
    assert spec.name == implement.name
    assert spec.kind is implement.kind
    assert spec.description
    assert spec.parameters["type"] == "object"


def test_there_is_no_shell_implement() -> None:
    names = default_registry().names()
    assert not [name for name in names if "shell" in name or "exec" in name]


# The registry


def test_registry_lookups() -> None:
    registry = default_registry()
    assert "noop.read" in registry
    assert "shell.exec" not in registry
    assert registry.find("shell.exec") is None
    assert registry.kind_of("noop.write") is ImplementKind.WRITE
    assert registry.kind_of("shell.exec") is None
    assert len(registry) == len(list(registry)) == 11
    assert registry.names() == tuple(sorted(registry.names()))
    with pytest.raises(UnknownImplementError, match=r"shell\.exec is not a registered implement"):
        registry.get("shell.exec")


def test_each_default_registry_has_its_own_instances() -> None:
    assert default_registry().get("noop.write") is not default_registry().get("noop.write")


def test_registering_the_same_name_twice_fails() -> None:
    registry = ImplementRegistry([NoopRead()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(NoopRead())


@pytest.mark.parametrize("name", ["nodot", "Upper.case", "has-hyphen.op", ".leading", "a..b", ""])
def test_implement_names_must_be_namespace_dot_operation(name: str) -> None:
    bad = NoopRead()
    bad.name = name  # type: ignore[misc]
    with pytest.raises(ValueError, match=r"must look like namespace\.operation"):
        ImplementRegistry().register(bad)


def test_an_implement_must_declare_kind_and_description() -> None:
    class NoKind(NoopRead):
        name: ClassVar[str] = "bad.kind"
        kind = "read"  # type: ignore[assignment]

    class NoDescription(NoopRead):
        name: ClassVar[str] = "bad.description"
        description: ClassVar[str] = ""

    with pytest.raises(ValueError, match="must declare kind"):
        ImplementRegistry().register(NoKind())
    with pytest.raises(ValueError, match="must have a description"):
        ImplementRegistry().register(NoDescription())
