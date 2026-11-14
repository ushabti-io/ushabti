"""Kubernetes implements: pods and deployments through the Kubernetes API.

The two read implements are built. The two write implements are still scaffold
stubs. All four need the ``kubernetes`` extra: pip install "ushabti[kubernetes]".
The extra is imported only when an implement runs, so the core imports without
it.

The field (the target) names the namespaces an ushabti tends, and may name a
kubeconfig context for runs outside the cluster::

    fields:
      - name: homelab-k3s
        kind: kubernetes
        namespaces: [default, monitoring]
        context: homelab   # only used outside the cluster

Inside a cluster the pod's service account is used. Outside it, the kubeconfig
that ``KUBECONFIG`` names, or ``~/.kube/config``, is read and never written. An
implement refuses a namespace the field does not list before it makes any call:
a spell can only look where it says it looks.

Results are shaped for Ma'at conditions. ``kubernetes.list_pods`` exposes
``not_ready``, the number of pods that are not running with every container
ready, so a spell can write ``expect: {path: not_ready, op: eq, value: 0}``.
``kubernetes.get_deployment`` exposes ``missing``, the desired replicas minus
the ready ones.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from pydantic import JsonValue

from ushabti._base import utc_now
from ushabti.errors import ImplementError, MissingExtraError, UshabtiError
from ushabti.field import Field, FieldKind
from ushabti.implements import Implement, ImplementKind, optional_text_argument, text_argument

if TYPE_CHECKING:
    from kubernetes.aio.client import V1ContainerStatus, V1Deployment, V1Pod

TIMEOUT_SECONDS: Final = 15.0
_DETAIL_LIMIT: Final = 300

_NAMESPACE: dict[str, JsonValue] = {
    "type": "string",
    "description": "Namespace to look in. Must be one of the field's namespaces.",
}
_DEPLOYMENT: dict[str, JsonValue] = {"type": "string", "description": "Name of the deployment."}


@dataclass(frozen=True, slots=True)
class Cluster:
    """The API objects one call uses. Tests build one around fakes."""

    core: Any
    """A ``CoreV1Api``, for pods."""

    apps: Any
    """An ``AppsV1Api``, for deployments."""


Connect = Callable[[Field], AbstractAsyncContextManager[Cluster]]
"""Opens the field's cluster for the duration of one call."""


class _KubernetesImplement(Implement):
    """What the Kubernetes reads share: the namespace check and the connection."""

    kind: ClassVar[ImplementKind] = ImplementKind.READ

    def __init__(
        self, *, connect: Connect | None = None, clock: Callable[[], datetime] = utc_now
    ) -> None:
        # Both are only given by tests: a fake cluster, and a fixed "now" for ages.
        self._connect = connect
        self._clock = clock

    def _scope(self, arguments: Mapping[str, JsonValue], field: Field | None) -> tuple[Field, str]:
        """The field and the namespace argument, refused unless the field lists it."""
        namespace = text_argument(self.name, arguments, "namespace")
        if field is None or field.kind is not FieldKind.KUBERNETES:
            raise ImplementError(
                f"{self.name} needs a field of kind kubernetes; "
                "name one on the assertion or the summons"
            )
        if not field.namespaces:
            raise ImplementError(
                f"{self.name}: field {field.name} lists no namespaces; add the ones it tends"
            )
        if namespace not in field.namespaces:
            raise ImplementError(
                f"{self.name}: namespace {namespace} is not one of field {field.name}'s "
                f"namespaces ({', '.join(field.namespaces)})"
            )
        return field, namespace

    async def _call[T](self, field: Field, request: Callable[[Cluster], Awaitable[T]]) -> T:
        """Open the cluster, make one request, and explain the client's failures."""
        connect = _connect if self._connect is None else self._connect
        try:
            async with connect(field) as cluster:
                return await request(cluster)
        except UshabtiError:
            raise
        except Exception as error:
            explained = _explain(self.name, error)
            if explained is None:
                raise
            raise explained from error


class ListPods(_KubernetesImplement):
    """The pods of one namespace: phase, readiness, restarts, and what is wrong."""

    name: ClassVar[str] = "kubernetes.list_pods"
    description: ClassVar[str] = (
        "List the pods in a namespace with their phase and readiness. Returns each pod, "
        "the count, `not_ready` (pods that are not running with every container ready, "
        "finished jobs excluded), the total restarts, and the reasons containers are "
        "waiting or have failed, such as CrashLoopBackOff."
    )
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
        field, namespace = self._scope(arguments, field)
        selector = optional_text_argument(self.name, arguments, "label_selector")

        async def request(cluster: Cluster) -> Any:
            return await cluster.core.list_namespaced_pod(
                namespace, label_selector=selector, _request_timeout=TIMEOUT_SECONDS
            )

        listed = await self._call(field, request)
        return _pods(namespace, selector, listed.items or [], self._clock())


class GetDeployment(_KubernetesImplement):
    """One deployment: how many replicas it wants, how many it has, and why not."""

    name: ClassVar[str] = "kubernetes.get_deployment"
    description: ClassVar[str] = (
        "Get one deployment with its desired and ready replica counts. Returns the "
        "counts under `replicas`, `missing` (desired minus ready), whether it is `ready`, "
        "its conditions, and the images it runs."
    )
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"namespace": _NAMESPACE, "name": _DEPLOYMENT},
        "required": ["namespace", "name"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        field, namespace = self._scope(arguments, field)
        name = text_argument(self.name, arguments, "name")

        async def request(cluster: Cluster) -> Any:
            return await cluster.apps.read_namespaced_deployment(
                name, namespace, _request_timeout=TIMEOUT_SECONDS
            )

        deployment = await self._call(field, request)
        return _deployment(namespace, name, deployment, self._clock())


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


# Reaching the cluster


@asynccontextmanager
async def _connect(field: Field) -> AsyncIterator[Cluster]:
    """The field's cluster: this pod's service account, or a kubeconfig context."""
    try:
        from kubernetes.aio import client, config
    except ImportError as error:
        raise MissingExtraError("kubernetes", "kubernetes") from error

    configuration = client.Configuration()
    try:
        config.load_incluster_config(client_configuration=configuration)  # type: ignore[no-untyped-call]
    except config.ConfigException:
        try:
            # Never persist: a refreshed token is not written back to the kubeconfig.
            await config.load_kube_config(  # type: ignore[no-untyped-call]
                context=field.context, client_configuration=configuration, persist_config=False
            )
        except config.ConfigException as error:
            raise ImplementError(
                f"kubernetes: no cluster to reach for field {field.name}: not inside one, "
                f"and no kubeconfig could be loaded ({error})"
            ) from error
    async with client.ApiClient(configuration) as api:
        yield Cluster(core=client.CoreV1Api(api), apps=client.AppsV1Api(api))


def _explain(name: str, error: Exception) -> ImplementError | None:
    """The ImplementError for a failure of the client, or None when the error is not one."""
    try:
        import aiohttp
        from kubernetes.aio.client import ApiException
    except ImportError:  # pragma: no cover  (only with the extra half installed)
        return None
    if isinstance(error, ApiException):
        return ImplementError(
            f"{name}: the cluster answered HTTP {error.status} ({error.reason}): "
            f"{_detail(error.body)}"
        )
    if isinstance(error, aiohttp.ClientError | TimeoutError | OSError):
        # The error text can carry the server address; the type name is enough.
        return ImplementError(f"{name}: could not reach the cluster ({type(error).__name__})")
    return None


def _detail(body: object) -> str:
    """The message of an API Status body, or the body itself, cut short."""
    text = body.decode(errors="replace") if isinstance(body, bytes) else str(body or "")
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    message = parsed.get("message") if isinstance(parsed, dict) else None
    detail = str(message) if message else text
    return detail[:_DETAIL_LIMIT] or "no detail given"


# Shaping the results


@dataclass(frozen=True, slots=True)
class _Pod:
    name: str | None
    phase: str | None
    ready: bool
    containers_ready: int
    containers: int
    restarts: int
    reasons: tuple[str, ...]
    node: str | None
    age_seconds: int | None

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "phase": self.phase,
            "ready": self.ready,
            "containers_ready": self.containers_ready,
            "containers": self.containers,
            "restarts": self.restarts,
            "reasons": list(self.reasons),
            "node": self.node,
            "age_seconds": self.age_seconds,
        }


def _pods(
    namespace: str, selector: str | None, items: list[V1Pod], now: datetime
) -> dict[str, JsonValue]:
    pods = [_pod(item, now) for item in items]
    counted = Counter(pod.phase or "Unknown" for pod in pods)
    phases: dict[str, JsonValue] = dict(sorted(counted.items()))
    reasons = cast("list[JsonValue]", sorted({reason for pod in pods for reason in pod.reasons}))
    listed: list[JsonValue] = [pod.as_json() for pod in pods]
    return {
        "namespace": namespace,
        "label_selector": selector,
        "count": len(pods),
        "not_ready": sum(1 for pod in pods if not pod.ready and pod.phase != "Succeeded"),
        "restarts": sum(pod.restarts for pod in pods),
        "phases": phases,
        "reasons": reasons,
        "pods": listed,
    }


def _pod(item: V1Pod, now: datetime) -> _Pod:
    metadata, spec, status = item.metadata, item.spec, item.status
    statuses = list(status.container_statuses or []) if status is not None else []
    init_statuses = list(status.init_container_statuses or []) if status is not None else []
    containers = len(spec.containers) if spec is not None else len(statuses)
    containers_ready = sum(1 for container in statuses if container.ready)
    phase = status.phase if status is not None else None
    reasons: set[str] = set()
    if status is not None and status.reason:
        reasons.add(status.reason)
    for container in (*init_statuses, *statuses):
        reasons.update(_container_reasons(container))
    return _Pod(
        name=metadata.name if metadata is not None else None,
        phase=phase,
        ready=phase == "Running" and containers > 0 and containers_ready == containers,
        containers_ready=containers_ready,
        containers=containers,
        restarts=sum(container.restart_count for container in statuses),
        reasons=tuple(sorted(reasons)),
        node=spec.node_name if spec is not None else None,
        age_seconds=_age(metadata.creation_timestamp if metadata is not None else None, now),
    )


def _container_reasons(status: V1ContainerStatus) -> set[str]:
    """Why a container is waiting or has stopped, such as CrashLoopBackOff."""
    found: set[str] = set()
    state = status.state
    if state is None:
        return found
    if state.waiting is not None and state.waiting.reason:
        found.add(state.waiting.reason)
    stopped = state.terminated.reason if state.terminated is not None else None
    if stopped and stopped != "Completed":
        found.add(stopped)
    return found


def _deployment(
    namespace: str, name: str, deployment: V1Deployment, now: datetime
) -> dict[str, JsonValue]:
    metadata, spec, status = deployment.metadata, deployment.spec, deployment.status
    desired = spec.replicas if spec.replicas is not None else 1
    ready = _count(status.ready_replicas if status is not None else None)
    generation = metadata.generation if metadata is not None else None
    observed = status.observed_generation if status is not None else None
    current = generation is None or observed is None or observed >= generation
    missing = max(desired - ready, 0)
    conditions: list[JsonValue] = [
        {
            "type": condition.type,
            "status": condition.status,
            "reason": condition.reason,
            "message": condition.message,
            "since": (
                condition.last_transition_time.isoformat()
                if condition.last_transition_time is not None
                else None
            ),
        }
        for condition in (status.conditions or [] if status is not None else [])
    ]
    template = spec.template.spec
    images: list[JsonValue] = [
        container.image
        for container in (template.containers if template is not None else [])
        if container.image
    ]
    return {
        "namespace": namespace,
        "name": name,
        "replicas": {
            "desired": desired,
            "ready": ready,
            "available": _count(status.available_replicas if status is not None else None),
            "updated": _count(status.updated_replicas if status is not None else None),
            "unavailable": _count(status.unavailable_replicas if status is not None else None),
        },
        "missing": missing,
        "ready": missing == 0 and current,
        "paused": bool(spec.paused),
        "generation": generation,
        "observed_generation": observed,
        "strategy": spec.strategy.type if spec.strategy is not None else None,
        "images": images,
        "conditions": conditions,
        "age_seconds": _age(metadata.creation_timestamp if metadata is not None else None, now),
    }


def _count(value: int | None) -> int:
    return 0 if value is None else value


def _age(created: datetime | None, now: datetime) -> int | None:
    """Seconds since creation. A naive timestamp is taken as UTC."""
    if created is None:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max(int((now - created).total_seconds()), 0)
