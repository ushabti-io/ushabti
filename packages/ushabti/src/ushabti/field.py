"""A field: a target an ushabti tends."""

from enum import StrEnum
from typing import Self

from pydantic import model_validator

from ushabti._base import Name, StrictModel


class FieldKind(StrEnum):
    KUBERNETES = "kubernetes"
    PROXMOX = "proxmox"
    PROMETHEUS = "prometheus"
    SERVICE = "service"


class Field(StrictModel):
    """A target an ushabti tends: a K3s cluster, a namespace, a Proxmox node, a service."""

    name: Name
    """Name of the field. A summons (a unit of work) refers to a field by this name."""

    kind: FieldKind
    """What the field is: `kubernetes`, `proxmox`, `prometheus`, or `service`."""

    namespaces: tuple[str, ...] = ()
    """Kubernetes namespaces to tend. Only valid when `kind` is `kubernetes`."""

    nodes: tuple[str, ...] = ()
    """Proxmox node names to tend. Only valid when `kind` is `proxmox`."""

    endpoint: str | None = None
    """Base URL of the field, usually an environment reference such as `${PROMETHEUS_URL}`."""

    @model_validator(mode="after")
    def _keys_match_kind(self) -> Self:
        if self.namespaces and self.kind is not FieldKind.KUBERNETES:
            raise ValueError("namespaces is only valid when kind is kubernetes")
        if self.nodes and self.kind is not FieldKind.PROXMOX:
            raise ValueError("nodes is only valid when kind is proxmox")
        return self
