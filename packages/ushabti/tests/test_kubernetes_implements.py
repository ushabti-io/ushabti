"""The Kubernetes read implements, answered by a fake cluster."""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import JsonValue

from sdk_testkit import weighed
from ushabti import Field, FieldKind, ImplementKind, MissingExtraError, Verdict, default_registry
from ushabti.errors import ImplementError, UnweighedWriteError
from ushabti.implements import Implement
from ushabti.implements.kubernetes import Cluster, GetDeployment, ListPods, _connect, implements
from ushabti.maat import Comparison, Condition

k8s = pytest.importorskip("kubernetes.aio.client")

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
HOMELAB = Field(name="homelab", kind=FieldKind.KUBERNETES, namespaces=("default", "monitoring"))


class FakeCluster:
    """Answers like the API server and remembers what it was asked."""

    def __init__(
        self,
        *,
        pods: list[Any] | None = None,
        deployment: Any = None,
        error: Exception | None = None,
    ) -> None:
        self.pods = pods or []
        self.deployment = deployment
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.open = 0

    @asynccontextmanager
    async def connect(self, field: Field) -> AsyncIterator[Cluster]:
        self.open += 1
        try:
            yield Cluster(core=self, apps=self)
        finally:
            self.open -= 1

    async def list_namespaced_pod(self, namespace: str, **options: Any) -> Any:
        self.calls.append(("list_namespaced_pod", (namespace,), options))
        if self.error is not None:
            raise self.error
        return k8s.V1PodList(items=self.pods)

    async def read_namespaced_deployment(self, name: str, namespace: str, **options: Any) -> Any:
        self.calls.append(("read_namespaced_deployment", (name, namespace), options))
        if self.error is not None:
            raise self.error
        return self.deployment


def list_pods(cluster: FakeCluster) -> ListPods:
    return ListPods(connect=cluster.connect, clock=lambda: NOW)


def get_deployment(cluster: FakeCluster) -> GetDeployment:
    return GetDeployment(connect=cluster.connect, clock=lambda: NOW)


def obj(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def rows(value: JsonValue) -> list[dict[str, JsonValue]]:
    assert isinstance(value, list)
    return [obj(item) for item in value]


# Building API objects the way the server would send them


def container(
    name: str,
    *,
    ready: bool = True,
    restarts: int = 0,
    waiting: str | None = None,
    terminated: str | None = None,
) -> Any:
    state = k8s.V1ContainerState(
        waiting=k8s.V1ContainerStateWaiting(reason=waiting) if waiting else None,
        terminated=(
            k8s.V1ContainerStateTerminated(reason=terminated, exit_code=0) if terminated else None
        ),
    )
    return k8s.V1ContainerStatus(
        name=name,
        ready=ready,
        restart_count=restarts,
        image="example/app:1",
        image_id="sha256:0",
        state=state,
    )


def pod(
    name: str,
    *,
    phase: str = "Running",
    containers: list[Any] | None = None,
    declared: int | None = None,
    init_containers: list[Any] | None = None,
    node: str = "node-a",
    age: timedelta = timedelta(hours=1),
    reason: str | None = None,
) -> Any:
    statuses = [container("app")] if containers is None else containers
    count = len(statuses) if declared is None else declared
    return k8s.V1Pod(
        metadata=k8s.V1ObjectMeta(name=name, namespace="default", creation_timestamp=NOW - age),
        spec=k8s.V1PodSpec(
            containers=[k8s.V1Container(name=f"c{i}") for i in range(count)], node_name=node
        ),
        status=k8s.V1PodStatus(
            phase=phase,
            reason=reason,
            container_statuses=statuses,
            init_container_statuses=init_containers,
        ),
    )


def deployment(
    *,
    desired: int | None = 3,
    ready: int | None = 2,
    available: int | None = 2,
    updated: int | None = 3,
    unavailable: int | None = 1,
    generation: int | None = 7,
    observed: int | None = 7,
    paused: bool = False,
    conditions: list[Any] | None = None,
    images: tuple[str, ...] = ("example/web:1.2",),
    with_status: bool = True,
) -> Any:
    status = k8s.V1DeploymentStatus(
        replicas=desired,
        ready_replicas=ready,
        available_replicas=available,
        updated_replicas=updated,
        unavailable_replicas=unavailable,
        observed_generation=observed,
        conditions=conditions,
    )
    return k8s.V1Deployment(
        metadata=k8s.V1ObjectMeta(
            name="web",
            namespace="default",
            generation=generation,
            creation_timestamp=NOW - timedelta(days=2),
        ),
        spec=k8s.V1DeploymentSpec(
            replicas=desired,
            paused=paused,
            strategy=k8s.V1DeploymentStrategy(type="RollingUpdate"),
            selector=k8s.V1LabelSelector(match_labels={"app": "web"}),
            template=k8s.V1PodTemplateSpec(
                spec=k8s.V1PodSpec(
                    containers=[
                        k8s.V1Container(name=f"c{i}", image=image) for i, image in enumerate(images)
                    ]
                )
            ),
        ),
        status=status if with_status else None,
    )


def condition(kind: str, status: str, reason: str, message: str = "") -> Any:
    return k8s.V1DeploymentCondition(
        type=kind,
        status=status,
        reason=reason,
        message=message,
        last_transition_time=NOW - timedelta(minutes=5),
    )


# Listing pods


async def test_list_pods_shapes_each_pod_and_counts_the_unready() -> None:
    cluster = FakeCluster(
        pods=[
            pod("web-1", age=timedelta(minutes=90)),
            pod(
                "web-2",
                containers=[container("app", ready=False, restarts=12, waiting="CrashLoopBackOff")],
                node="node-b",
            ),
            pod(
                "job-9",
                phase="Succeeded",
                containers=[container("run", ready=False, terminated="Completed")],
            ),
        ]
    )
    result = await list_pods(cluster).run({"namespace": "default"}, field=HOMELAB)

    assert result == {
        "namespace": "default",
        "label_selector": None,
        "count": 3,
        "not_ready": 1,
        "restarts": 12,
        "phases": {"Running": 2, "Succeeded": 1},
        "reasons": ["CrashLoopBackOff"],
        "pods": [
            {
                "name": "web-1",
                "phase": "Running",
                "ready": True,
                "containers_ready": 1,
                "containers": 1,
                "restarts": 0,
                "reasons": [],
                "node": "node-a",
                "age_seconds": 5400,
            },
            {
                "name": "web-2",
                "phase": "Running",
                "ready": False,
                "containers_ready": 0,
                "containers": 1,
                "restarts": 12,
                "reasons": ["CrashLoopBackOff"],
                "node": "node-b",
                "age_seconds": 3600,
            },
            {
                "name": "job-9",
                "phase": "Succeeded",
                "ready": False,
                "containers_ready": 0,
                "containers": 1,
                "restarts": 0,
                "reasons": [],
                "node": "node-a",
                "age_seconds": 3600,
            },
        ],
    }
    assert cluster.calls == [
        ("list_namespaced_pod", ("default",), {"label_selector": None, "_request_timeout": 15.0})
    ]
    assert cluster.open == 0


async def test_a_pending_pod_counts_the_containers_it_declares() -> None:
    stuck = pod(
        "db-0",
        phase="Pending",
        containers=[],
        declared=2,
        init_containers=[container("init-schema", ready=False, waiting="ImagePullBackOff")],
    )
    cluster = FakeCluster(pods=[stuck])
    result = obj(await list_pods(cluster).run({"namespace": "default"}, field=HOMELAB))

    assert result["not_ready"] == 1
    assert result["reasons"] == ["ImagePullBackOff"]
    first = rows(result["pods"])[0]
    assert (first["containers"], first["containers_ready"], first["ready"]) == (2, 0, False)


async def test_a_pod_level_reason_such_as_evicted_is_reported() -> None:
    gone = pod("cache-1", phase="Failed", containers=[], declared=1, reason="Evicted")
    cluster = FakeCluster(pods=[gone])
    result = obj(await list_pods(cluster).run({"namespace": "default"}, field=HOMELAB))
    assert result["not_ready"] == 1
    assert result["reasons"] == ["Evicted"]
    assert result["phases"] == {"Failed": 1}


async def test_the_label_selector_reaches_the_cluster() -> None:
    cluster = FakeCluster()
    result = await list_pods(cluster).run(
        {"namespace": "monitoring", "label_selector": "app=web"}, field=HOMELAB
    )
    assert cluster.calls[0][1:] == (
        ("monitoring",),
        {"label_selector": "app=web", "_request_timeout": 15.0},
    )
    assert result == {
        "namespace": "monitoring",
        "label_selector": "app=web",
        "count": 0,
        "not_ready": 0,
        "restarts": 0,
        "phases": {},
        "reasons": [],
        "pods": [],
    }


# Reading a deployment


async def test_get_deployment_shapes_replicas_conditions_and_images() -> None:
    message = "Deployment does not have minimum availability."
    cluster = FakeCluster(
        deployment=deployment(
            conditions=[condition("Available", "False", "MinimumReplicasUnavailable", message)]
        )
    )
    arguments = {"namespace": "default", "name": "web"}
    result = await get_deployment(cluster).run(arguments, field=HOMELAB)

    assert result == {
        "namespace": "default",
        "name": "web",
        "replicas": {"desired": 3, "ready": 2, "available": 2, "updated": 3, "unavailable": 1},
        "missing": 1,
        "ready": False,
        "paused": False,
        "generation": 7,
        "observed_generation": 7,
        "strategy": "RollingUpdate",
        "images": ["example/web:1.2"],
        "conditions": [
            {
                "type": "Available",
                "status": "False",
                "reason": "MinimumReplicasUnavailable",
                "message": message,
                "since": "2026-11-13T11:55:00+00:00",
            }
        ],
        "age_seconds": 172800,
    }
    assert cluster.calls == [
        ("read_namespaced_deployment", ("web", "default"), {"_request_timeout": 15.0})
    ]


async def test_a_settled_deployment_is_ready() -> None:
    settled = deployment(desired=2, ready=2, available=2, updated=2, unavailable=None)
    result = obj(
        await get_deployment(FakeCluster(deployment=settled)).run(
            {"namespace": "default", "name": "web"}, field=HOMELAB
        )
    )
    assert result["missing"] == 0
    assert result["ready"] is True
    assert obj(result["replicas"])["unavailable"] == 0


async def test_a_deployment_the_controller_has_not_caught_up_with_is_not_ready() -> None:
    stale = deployment(desired=2, ready=2, available=2, updated=2, generation=8, observed=7)
    result = obj(
        await get_deployment(FakeCluster(deployment=stale)).run(
            {"namespace": "default", "name": "web"}, field=HOMELAB
        )
    )
    assert result["missing"] == 0
    assert result["ready"] is False


async def test_a_deployment_without_a_replica_count_or_a_status_wants_one() -> None:
    fresh = deployment(desired=None, generation=None, with_status=False, images=())
    result = obj(
        await get_deployment(FakeCluster(deployment=fresh)).run(
            {"namespace": "default", "name": "web"}, field=HOMELAB
        )
    )
    assert result["replicas"] == {
        "desired": 1,
        "ready": 0,
        "available": 0,
        "updated": 0,
        "unavailable": 0,
    }
    assert result["missing"] == 1
    assert result["ready"] is False
    assert result["observed_generation"] is None
    assert result["conditions"] == []
    assert result["images"] == []


# Scope: the field decides where an implement may look


async def test_a_namespace_outside_the_field_is_refused_before_any_call() -> None:
    cluster = FakeCluster()
    with pytest.raises(ImplementError, match="is not one of field homelab's namespaces"):
        await list_pods(cluster).run({"namespace": "kube-system"}, field=HOMELAB)
    with pytest.raises(ImplementError, match=r"namespaces \(default, monitoring\)"):
        await get_deployment(cluster).run(
            {"namespace": "kube-system", "name": "coredns"}, field=HOMELAB
        )
    assert cluster.calls == []
    assert cluster.open == 0


@pytest.mark.parametrize(
    ("field", "complaint"),
    [
        (None, "needs a field of kind kubernetes"),
        (
            Field(name="metrics", kind=FieldKind.PROMETHEUS, endpoint="http://m.example.invalid"),
            "needs a field of kind kubernetes",
        ),
        (Field(name="bare", kind=FieldKind.KUBERNETES), "field bare lists no namespaces"),
    ],
    ids=["no field", "prometheus field", "no namespaces"],
)
async def test_a_field_that_does_not_scope_the_call_is_refused(
    field: Field | None, complaint: str
) -> None:
    cluster = FakeCluster()
    with pytest.raises(ImplementError, match=complaint):
        await list_pods(cluster).run({"namespace": "default"}, field=field)
    assert cluster.calls == []


async def test_arguments_are_checked_before_the_cluster_is_opened() -> None:
    cluster = FakeCluster()
    with pytest.raises(ImplementError, match="the argument 'namespace' is required"):
        await list_pods(cluster).run({}, field=HOMELAB)
    with pytest.raises(ImplementError, match="the argument 'name' is required"):
        await get_deployment(cluster).run({"namespace": "default"}, field=HOMELAB)
    selected: dict[str, JsonValue] = {"namespace": "default", "label_selector": True}
    with pytest.raises(ImplementError, match="the argument 'label_selector' must be a string"):
        await list_pods(cluster).run(selected, field=HOMELAB)
    with pytest.raises(ImplementError, match="the argument 'namespace' must be a string"):
        await list_pods(cluster).run({"namespace": ["default"]}, field=HOMELAB)
    assert cluster.calls == []


def test_a_context_is_only_valid_on_a_kubernetes_field() -> None:
    assert Field(name="lab", kind=FieldKind.KUBERNETES, context="homelab").context == "homelab"
    with pytest.raises(ValueError, match="context is only valid when kind is kubernetes"):
        Field(name="pve", kind=FieldKind.PROXMOX, context="homelab")


# Failures


def api_error(status: int, reason: str, body: object) -> Any:
    error = k8s.ApiException(status=status, reason=reason)
    error.body = body
    return error


async def test_an_api_error_names_the_status_and_the_message() -> None:
    body = (
        '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure",'
        '"message":"deployments.apps \\"web\\" not found","reason":"NotFound","code":404}'
    )
    cluster = FakeCluster(error=api_error(404, "Not Found", body))
    with pytest.raises(ImplementError) as caught:
        await get_deployment(cluster).run({"namespace": "default", "name": "web"}, field=HOMELAB)
    assert str(caught.value) == (
        "kubernetes.get_deployment: the cluster answered HTTP 404 (Not Found): "
        'deployments.apps "web" not found'
    )
    assert cluster.open == 0


async def test_an_api_error_without_a_json_message_shows_its_text_cut_short() -> None:
    plain = FakeCluster(error=api_error(403, "Forbidden", "pods is forbidden"))
    with pytest.raises(ImplementError) as caught:
        await list_pods(plain).run({"namespace": "default"}, field=HOMELAB)
    assert str(caught.value) == (
        "kubernetes.list_pods: the cluster answered HTTP 403 (Forbidden): pods is forbidden"
    )

    silent = FakeCluster(error=api_error(500, "Internal Server Error", None))
    with pytest.raises(ImplementError, match=r"\(Internal Server Error\): no detail given"):
        await list_pods(silent).run({"namespace": "default"}, field=HOMELAB)

    verbose = FakeCluster(error=api_error(400, "Bad Request", b"x" * 1000))
    with pytest.raises(ImplementError) as caught:
        await list_pods(verbose).run({"namespace": "default"}, field=HOMELAB)
    assert len(str(caught.value)) < 400


async def test_a_connection_failure_does_not_echo_the_server() -> None:
    aiohttp = pytest.importorskip("aiohttp")
    server = "https://user:secret@cluster.example.invalid:6443"
    failures: list[Exception] = [
        aiohttp.ClientConnectionError(server),
        TimeoutError(server),
        ConnectionRefusedError(111, server),
    ]
    for failure in failures:
        cluster = FakeCluster(error=failure)
        with pytest.raises(ImplementError) as caught:
            await list_pods(cluster).run({"namespace": "default"}, field=HOMELAB)
        assert str(caught.value) == (
            f"kubernetes.list_pods: could not reach the cluster ({type(failure).__name__})"
        )
        assert "example.invalid" not in str(caught.value)
        assert "secret" not in str(caught.value)


async def test_an_unexpected_error_is_not_swallowed() -> None:
    cluster = FakeCluster(error=RuntimeError("a bug in the fake"))
    with pytest.raises(RuntimeError, match="a bug in the fake"):
        await list_pods(cluster).run({"namespace": "default"}, field=HOMELAB)
    assert cluster.open == 0


# Reaching a cluster


async def test_without_the_extra_the_implement_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "kubernetes", None)
    monkeypatch.setitem(sys.modules, "kubernetes.aio", None)
    with pytest.raises(MissingExtraError) as caught:
        await ListPods().run({"namespace": "default"}, field=HOMELAB)
    assert caught.value.extra == "kubernetes"
    assert 'pip install "ushabti[kubernetes]"' in str(caught.value)


async def test_connect_tries_the_cluster_first_and_then_the_kubeconfig_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = pytest.importorskip("kubernetes.aio.config")
    seen: dict[str, Any] = {}

    def not_in_a_pod(client_configuration: Any = None, try_refresh_token: bool = True) -> None:
        seen["in_cluster"] = True
        raise config.ConfigException("Service host/port is not set.")

    async def from_the_file(**options: Any) -> None:
        seen["kube_config"] = options
        options["client_configuration"].host = "https://cluster.example.invalid:6443"

    monkeypatch.setattr(config, "load_incluster_config", not_in_a_pod)
    monkeypatch.setattr(config, "load_kube_config", from_the_file)
    field = Field(name="lab", kind=FieldKind.KUBERNETES, namespaces=("default",), context="homelab")

    async with _connect(field) as cluster:
        assert isinstance(cluster.core, k8s.CoreV1Api)
        assert isinstance(cluster.apps, k8s.AppsV1Api)
        assert cluster.core.api_client.configuration.host == "https://cluster.example.invalid:6443"

    assert seen["in_cluster"] is True
    assert seen["kube_config"]["context"] == "homelab"
    assert seen["kube_config"]["persist_config"] is False


async def test_connect_inside_a_cluster_uses_the_service_account_and_ignores_the_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = pytest.importorskip("kubernetes.aio.config")

    def in_a_pod(client_configuration: Any = None, try_refresh_token: bool = True) -> None:
        client_configuration.host = "https://kubernetes.default.svc"

    async def never(**options: Any) -> None:
        raise AssertionError("the kubeconfig must not be read inside a cluster")

    monkeypatch.setattr(config, "load_incluster_config", in_a_pod)
    monkeypatch.setattr(config, "load_kube_config", never)
    field = Field(name="lab", kind=FieldKind.KUBERNETES, namespaces=("default",), context="homelab")

    async with _connect(field) as cluster:
        assert cluster.core.api_client.configuration.host == "https://kubernetes.default.svc"


async def test_connect_without_any_configuration_is_an_implement_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = pytest.importorskip("kubernetes.aio.config")

    def nothing(client_configuration: Any = None, try_refresh_token: bool = True) -> None:
        raise config.ConfigException("Service host/port is not set.")

    async def nothing_either(**options: Any) -> None:
        raise config.ConfigException("Invalid kube-config file. No configuration found.")

    monkeypatch.setattr(config, "load_incluster_config", nothing)
    monkeypatch.setattr(config, "load_kube_config", nothing_either)
    field = Field(name="lab", kind=FieldKind.KUBERNETES, namespaces=("default",))

    with pytest.raises(ImplementError, match="no cluster to reach for field lab"):
        async with _connect(field):
            pass


# The guard and the registry


@pytest.mark.parametrize("implement", implements()[:2], ids=lambda i: i.name)
async def test_the_reads_need_no_verdict_but_refuse_a_failed_one(implement: Implement) -> None:
    assert implement.kind is ImplementKind.READ
    with pytest.raises(UnweighedWriteError, match="only PASS may run"):
        await implement.run({}, weighed=weighed(implement.name, Verdict.REJECT))
    with pytest.raises(UnweighedWriteError, match=r"the verdict is for noop\.read"):
        await implement.run({}, weighed=weighed("noop.read"))


def test_the_registry_holds_the_four_kubernetes_implements() -> None:
    registry = default_registry()
    assert [i.name for i in implements()] == [
        "kubernetes.list_pods",
        "kubernetes.get_deployment",
        "kubernetes.restart_deployment",
        "kubernetes.scale_deployment",
    ]
    assert isinstance(registry.get("kubernetes.list_pods"), ListPods)
    assert isinstance(registry.get("kubernetes.get_deployment"), GetDeployment)


# Ma'at


async def test_the_results_answer_maat_conditions() -> None:
    pods = await list_pods(FakeCluster(pods=[pod("web-1")])).run(
        {"namespace": "default"}, field=HOMELAB
    )
    assert Condition(path="not_ready", op=Comparison.EQ, value=0).evaluate(pods) == (True, 0)

    web = await get_deployment(FakeCluster(deployment=deployment())).run(
        {"namespace": "default", "name": "web"}, field=HOMELAB
    )
    assert Condition(path="missing", op=Comparison.EQ, value=0).evaluate(web) == (False, 1)
    assert Condition(path="replicas.ready", op=Comparison.GE, value=2).evaluate(web) == (True, 2)


# Ages


async def test_ages_come_from_the_clock_and_a_naive_timestamp_is_utc() -> None:
    aware = pod("a", age=timedelta(seconds=90))
    naive = pod("b")
    naive.metadata.creation_timestamp = (NOW - timedelta(seconds=90)).replace(tzinfo=None)
    unknown = pod("c")
    unknown.metadata.creation_timestamp = None
    future = pod("d", age=timedelta(seconds=-30))

    result = await list_pods(FakeCluster(pods=[aware, naive, unknown, future])).run(
        {"namespace": "default"}, field=HOMELAB
    )
    assert [row["age_seconds"] for row in rows(obj(result)["pods"])] == [90, 90, None, 0]
