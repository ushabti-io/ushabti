"""Kubernetes implements. Scaffold stubs: the operations are declared, not built.

Real versions need the ``kubernetes`` extra: pip install "ushabti[kubernetes]"
"""

from collections.abc import Mapping
from typing import ClassVar

from pydantic import JsonValue

from ushabti.field import Field
from ushabti.implements import Implement, ImplementKind

_NAMESPACE: dict[str, JsonValue] = {
    "type": "string",
    "description": "Namespace to look in. Must be one of the field's namespaces.",
}
_DEPLOYMENT: dict[str, JsonValue] = {"type": "string", "description": "Name of the deployment."}


class ListPods(Implement):
    name: ClassVar[str] = "kubernetes.list_pods"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "List the pods in a namespace with their phase and readiness."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {
            "namespace": _NAMESPACE,
            "label_selector": {
                "type": "string",
                "description": "Optional label selector, such as app=web.",
            },
        },
        "required": ["namespace"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): list pods in the field's cluster with the Kubernetes client.
        raise NotImplementedError(
            "kubernetes.list_pods is a scaffold stub: listing pods through the "
            "Kubernetes client (the `kubernetes` extra) is not implemented yet"
        )


class GetDeployment(Implement):
    name: ClassVar[str] = "kubernetes.get_deployment"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Get one deployment with its desired and ready replica counts."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"namespace": _NAMESPACE, "name": _DEPLOYMENT},
        "required": ["namespace", "name"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): read the deployment and its status with the Kubernetes client.
        raise NotImplementedError(
            "kubernetes.get_deployment is a scaffold stub: reading a deployment through "
            "the Kubernetes client (the `kubernetes` extra) is not implemented yet"
        )


class RestartDeployment(Implement):
    name: ClassVar[str] = "kubernetes.restart_deployment"
    kind: ClassVar[ImplementKind] = ImplementKind.WRITE
    description: ClassVar[str] = "Restart a deployment by rolling its pods."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"namespace": _NAMESPACE, "name": _DEPLOYMENT},
        "required": ["namespace", "name"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): trigger a rolling restart of the deployment with the Kubernetes client.
        raise NotImplementedError(
            "kubernetes.restart_deployment is a scaffold stub: the rolling restart through "
            "the Kubernetes client (the `kubernetes` extra) is not implemented yet"
        )


class ScaleDeployment(Implement):
    name: ClassVar[str] = "kubernetes.scale_deployment"
    kind: ClassVar[ImplementKind] = ImplementKind.WRITE
    description: ClassVar[str] = "Set the replica count of a deployment."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {
            "namespace": _NAMESPACE,
            "name": _DEPLOYMENT,
            "replicas": {
                "type": "integer",
                "minimum": 0,
                "description": "Replica count to set.",
            },
        },
        "required": ["namespace", "name", "replicas"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): set the deployment's replica count with the Kubernetes client.
        raise NotImplementedError(
            "kubernetes.scale_deployment is a scaffold stub: scaling a deployment through "
            "the Kubernetes client (the `kubernetes` extra) is not implemented yet"
        )


def implements() -> tuple[Implement, ...]:
    return (ListPods(), GetDeployment(), RestartDeployment(), ScaleDeployment())
