"""Proxmox implements. Scaffold stubs: the operations are declared, not built.

Real versions need the ``proxmox`` extra: pip install "ushabti[proxmox]"
"""

from collections.abc import Mapping
from typing import ClassVar

from pydantic import JsonValue

from ushabti.field import Field
from ushabti.implements import Implement, ImplementKind

_NODE: dict[str, JsonValue] = {
    "type": "string",
    "description": "Name of the Proxmox node. Must be one of the field's nodes.",
}


class NodeStatus(Implement):
    name: ClassVar[str] = "proxmox.node_status"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Get the status of a Proxmox node: uptime, CPU, memory, storage."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"node": _NODE},
        "required": ["node"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): read the node status with the Proxmox API client.
        raise NotImplementedError(
            "proxmox.node_status is a scaffold stub: reading node status through "
            "proxmoxer (the `proxmox` extra) is not implemented yet"
        )


class ListVms(Implement):
    name: ClassVar[str] = "proxmox.list_vms"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "List the virtual machines on a Proxmox node with their state."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"node": _NODE},
        "required": ["node"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): list the node's virtual machines with the Proxmox API client.
        raise NotImplementedError(
            "proxmox.list_vms is a scaffold stub: listing virtual machines through "
            "proxmoxer (the `proxmox` extra) is not implemented yet"
        )


class StartVm(Implement):
    name: ClassVar[str] = "proxmox.start_vm"
    kind: ClassVar[ImplementKind] = ImplementKind.WRITE
    description: ClassVar[str] = "Start a stopped virtual machine on a Proxmox node."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {
            "node": _NODE,
            "vmid": {"type": "integer", "description": "Numeric id of the virtual machine."},
        },
        "required": ["node", "vmid"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): start the virtual machine with the Proxmox API client.
        raise NotImplementedError(
            "proxmox.start_vm is a scaffold stub: starting a virtual machine through "
            "proxmoxer (the `proxmox` extra) is not implemented yet"
        )


def implements() -> tuple[Implement, ...]:
    return (NodeStatus(), ListVms(), StartVm())
