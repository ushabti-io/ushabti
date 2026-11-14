"""The Prometheus implements, answered by a fake server."""

import json
import sys
from typing import Any

import pytest

from sdk_testkit import weighed
from ushabti import Field, FieldKind, ImplementKind, MissingExtraError, Verdict, default_registry
from ushabti.errors import ImplementError, UnweighedWriteError
from ushabti.implements.prometheus import Query, QueryRange, implements

httpx = pytest.importorskip("httpx")

ENDPOINT = "http://prometheus.example.invalid:9090"
METRICS = Field(name="metrics", kind=FieldKind.PROMETHEUS, endpoint=ENDPOINT)


class FakePrometheus:
    """Answers like Prometheus and remembers what it was asked."""

    def __init__(self, status: int = 200, body: Any = None, raise_error: Exception | None = None):
        self.status = status
        self.body = body
        self.raise_error = raise_error
        self.requests: list[Any] = []

    def __call__(self, request: Any) -> Any:
        self.requests.append(request)
        if self.raise_error is not None:
            raise self.raise_error
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body)
        return httpx.Response(self.status, json=self.body)

    @property
    def params(self) -> dict[str, str]:
        return dict(self.requests[-1].url.params)


def success(result_type: str, result: Any) -> dict[str, Any]:
    return {"status": "success", "data": {"resultType": result_type, "result": result}}


def sample(value: str, **labels: str) -> dict[str, Any]:
    return {"metric": labels, "value": [1762680000.5, value]}


def query(server: FakePrometheus) -> Query:
    return Query(transport=httpx.MockTransport(server))


def query_range(server: FakePrometheus) -> QueryRange:
    return QueryRange(transport=httpx.MockTransport(server))


# Instant queries


async def test_one_sample_is_exposed_at_value() -> None:
    server = FakePrometheus(body=success("vector", [sample("84.37", job="node")]))
    result = await query(server).run({"query": "max(memory_used_percent)"}, field=METRICS)

    assert result == {
        "query": "max(memory_used_percent)",
        "result_type": "vector",
        "count": 1,
        "value": 84.37,
        "samples": [{"metric": {"job": "node"}, "timestamp": 1762680000.5, "value": 84.37}],
    }
    request = server.requests[0]
    assert request.method == "GET"
    assert str(request.url) == f"{ENDPOINT}/api/v1/query?query=max%28memory_used_percent%29"


async def test_several_samples_have_no_single_value() -> None:
    body = success("vector", [sample("1", instance="a"), sample("0", instance="b")])
    result = await query(FakePrometheus(body=body)).run({"query": "up"}, field=METRICS)
    assert isinstance(result, dict)
    assert result["count"] == 2
    assert result["value"] is None
    assert [s["metric"]["instance"] for s in result["samples"]] == ["a", "b"]  # type: ignore[index, union-attr, call-overload]


async def test_an_empty_result_is_not_an_error() -> None:
    result = await query(FakePrometheus(body=success("vector", []))).run(
        {"query": 'up{job="x"}'}, field=METRICS
    )
    assert result == {
        "query": 'up{job="x"}',
        "result_type": "vector",
        "count": 0,
        "value": None,
        "samples": [],
    }


async def test_scalar_and_string_results() -> None:
    scalar = await query(FakePrometheus(body=success("scalar", [1762680000, "3"]))).run(
        {"query": "3"}, field=METRICS
    )
    assert isinstance(scalar, dict)
    assert scalar["value"] == 3.0
    assert scalar["result_type"] == "scalar"

    text = await query(FakePrometheus(body=success("string", [1762680000, "hello"]))).run(
        {"query": '"hello"'}, field=METRICS
    )
    assert text == {
        "query": '"hello"',
        "result_type": "string",
        "count": 1,
        "value": None,
        "text": "hello",
        "timestamp": 1762680000.0,
    }


@pytest.mark.parametrize("text", ["NaN", "+Inf", "-Inf"])
async def test_nan_and_infinity_become_null_so_maat_treats_them_as_unknown(text: str) -> None:
    result = await query(FakePrometheus(body=success("vector", [sample(text)]))).run(
        {"query": "x"}, field=METRICS
    )
    assert isinstance(result, dict)
    assert result["value"] is None
    assert json.dumps(result)  # the whole observation is plain JSON


async def test_the_evaluation_time_is_passed_through() -> None:
    server = FakePrometheus(body=success("vector", []))
    await query(server).run({"query": "up", "time": "2026-01-02T03:04:05Z"}, field=METRICS)
    assert server.params == {"query": "up", "time": "2026-01-02T03:04:05Z"}
    await query(server).run({"query": "up", "time": 1762680000}, field=METRICS)
    assert server.params["time"] == "1762680000"


async def test_an_instant_query_with_a_range_selector_returns_a_matrix() -> None:
    body = success("matrix", [{"metric": {}, "values": [[1, "1"], [2, "2"]]}])
    result = await query(FakePrometheus(body=body)).run({"query": "up[2m]"}, field=METRICS)
    assert isinstance(result, dict)
    assert result["result_type"] == "matrix"
    assert result["last"] == 2.0


# Range queries


async def test_a_range_query_shapes_each_series_and_the_single_series_shortcuts() -> None:
    body = success(
        "matrix",
        [{"metric": {"instance": "a"}, "values": [[1, "4"], [2, "NaN"], [3, "9"], [4, "2"]]}],
    )
    server = FakePrometheus(body=body)
    result = await query_range(server).run(
        {"query": "rate(x[5m])", "start": "1762680000", "end": "1762683600", "step": "60s"},
        field=METRICS,
    )

    assert server.params == {
        "query": "rate(x[5m])",
        "start": "1762680000",
        "end": "1762683600",
        "step": "60s",
    }
    assert str(server.requests[0].url).startswith(f"{ENDPOINT}/api/v1/query_range?")
    assert result == {
        "query": "rate(x[5m])",
        "result_type": "matrix",
        "count": 1,
        "first": 4.0,
        "last": 2.0,
        "min": 2.0,
        "max": 9.0,
        "series": [
            {
                "metric": {"instance": "a"},
                "values": [[1.0, 4.0], [2.0, None], [3.0, 9.0], [4.0, 2.0]],
                "first": 4.0,
                "last": 2.0,
                "min": 2.0,
                "max": 9.0,
            }
        ],
    }


async def test_several_series_keep_their_own_numbers_only() -> None:
    body = success(
        "matrix",
        [
            {"metric": {"i": "a"}, "values": [[1, "1"]]},
            {"metric": {"i": "b"}, "values": [[1, "5"]]},
        ],
    )
    result = await query_range(FakePrometheus(body=body)).run(
        {"query": "x", "start": "1", "end": "2", "step": "1"}, field=METRICS
    )
    assert isinstance(result, dict)
    assert result["count"] == 2
    assert (result["first"], result["last"], result["min"], result["max"]) == (
        None,
        None,
        None,
        None,
    )
    assert [s["max"] for s in result["series"]] == [1.0, 5.0]  # type: ignore[index, union-attr, call-overload]


async def test_a_series_with_no_finite_values() -> None:
    body = success("matrix", [{"metric": {}, "values": [[1, "NaN"]]}])
    result = await query_range(FakePrometheus(body=body)).run(
        {"query": "x", "start": "1", "end": "2", "step": "1"}, field=METRICS
    )
    assert isinstance(result, dict)
    assert result["min"] is None
    assert result["last"] is None


@pytest.mark.parametrize("missing", ["query", "start", "end", "step"])
async def test_a_range_query_needs_all_four_arguments(missing: str) -> None:
    arguments: dict[str, Any] = {"query": "x", "start": "1", "end": "2", "step": "1"}
    del arguments[missing]
    with pytest.raises(ImplementError, match=f"the argument '{missing}' is required"):
        await query_range(FakePrometheus()).run(arguments, field=METRICS)


# Arguments


async def test_the_query_is_required_and_must_be_text() -> None:
    with pytest.raises(ImplementError, match="the argument 'query' is required"):
        await query(FakePrometheus()).run({}, field=METRICS)
    with pytest.raises(ImplementError, match="the argument 'query' must be a string"):
        await query(FakePrometheus()).run({"query": ["up"]}, field=METRICS)
    with pytest.raises(ImplementError, match="the argument 'query' must be a string"):
        await query(FakePrometheus()).run({"query": True}, field=METRICS)


# The field and the endpoint


async def test_the_field_must_be_a_prometheus_field_with_an_endpoint() -> None:
    server = FakePrometheus(body=success("vector", []))
    with pytest.raises(ImplementError, match="needs a field of kind prometheus"):
        await query(server).run({"query": "up"})
    with pytest.raises(ImplementError, match="needs a field of kind prometheus"):
        await query(server).run({"query": "up"}, field=Field(name="k3s", kind=FieldKind.KUBERNETES))
    with pytest.raises(ImplementError, match="has no endpoint"):
        await query(server).run(
            {"query": "up"}, field=Field(name="bare", kind=FieldKind.PROMETHEUS)
        )
    assert server.requests == []


async def test_the_endpoint_may_be_an_environment_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakePrometheus(body=success("vector", []))
    field = Field(name="metrics", kind=FieldKind.PROMETHEUS, endpoint="${TEST_PROMETHEUS_URL}")
    monkeypatch.delenv("TEST_PROMETHEUS_URL", raising=False)
    with pytest.raises(
        ImplementError,
        match=r"field metrics: environment variable not set: \$\{TEST_PROMETHEUS_URL\}",
    ):
        await query(server).run({"query": "up"}, field=field)

    monkeypatch.setenv("TEST_PROMETHEUS_URL", "http://resolved.example.invalid/prometheus")
    await query(server).run({"query": "up"}, field=field)
    assert (
        str(server.requests[0].url)
        == "http://resolved.example.invalid/prometheus/api/v1/query?query=up"
    )


# Failures


async def test_a_rejected_query_carries_prometheus_reason() -> None:
    body = {
        "status": "error",
        "errorType": "bad_data",
        "error": (
            'invalid parameter "query": 1:4: parse error: unexpected end of input inside braces'
        ),
    }
    with pytest.raises(ImplementError) as caught:
        await query(FakePrometheus(status=400, body=body)).run({"query": "up{"}, field=METRICS)
    assert str(caught.value) == (
        "prometheus.query: Prometheus rejected the query (bad_data): "
        'invalid parameter "query": 1:4: parse error: unexpected end of input inside braces'
    )


async def test_a_long_reason_is_cut_short() -> None:
    body = {"status": "error", "errorType": "execution", "error": "x" * 1000}
    with pytest.raises(ImplementError) as caught:
        await query(FakePrometheus(status=422, body=body)).run({"query": "up"}, field=METRICS)
    assert len(str(caught.value)) < 400


async def test_a_gateway_error_page_is_reported_without_crashing() -> None:
    with pytest.raises(ImplementError, match="answered HTTP 502 with a body that is not JSON"):
        await query(FakePrometheus(status=502, body="<html>bad gateway</html>")).run(
            {"query": "up"}, field=METRICS
        )
    with pytest.raises(ImplementError, match="answered HTTP 200 with no object"):
        await query(FakePrometheus(body=[1, 2])).run({"query": "up"}, field=METRICS)
    with pytest.raises(ImplementError, match="answered without a data object"):
        await query(FakePrometheus(body={"status": "success"})).run({"query": "up"}, field=METRICS)


async def test_an_unreachable_server_names_the_failure_but_not_the_url() -> None:
    error = httpx.ConnectError("connection refused")
    with pytest.raises(ImplementError) as caught:
        await query(FakePrometheus(raise_error=error)).run({"query": "up"}, field=METRICS)
    assert str(caught.value) == "prometheus.query: could not reach Prometheus (ConnectError)"
    assert "example.invalid" not in str(caught.value)


async def test_an_unexpected_shape_is_an_error_not_a_crash() -> None:
    with pytest.raises(ImplementError, match="returned a vector in an unexpected shape"):
        await query(FakePrometheus(body=success("vector", [{"metric": {}}]))).run(
            {"query": "up"}, field=METRICS
        )
    with pytest.raises(ImplementError, match="unknown result type: 'blob'"):
        await query(FakePrometheus(body=success("blob", []))).run({"query": "up"}, field=METRICS)


async def test_without_the_extra_the_error_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "httpx", None)
    with pytest.raises(MissingExtraError, match=r'pip install "ushabti\[prometheus\]"'):
        await Query().run({"query": "up"}, field=METRICS)


# Registration and the guard


def test_both_are_read_implements_and_registered() -> None:
    registry = default_registry()
    assert registry.kind_of("prometheus.query") is ImplementKind.READ
    assert registry.kind_of("prometheus.query_range") is ImplementKind.READ
    assert [i.name for i in implements()] == ["prometheus.query", "prometheus.query_range"]


async def test_a_verdict_other_than_pass_still_refuses_a_read() -> None:
    server = FakePrometheus(body=success("vector", []))
    with pytest.raises(UnweighedWriteError):
        await query(server).run(
            {"query": "up"}, field=METRICS, weighed=weighed("prometheus.query", Verdict.REJECT)
        )
    assert server.requests == []


def test_the_schemas_name_every_argument() -> None:
    assert set(Query().spec().parameters["properties"]) == {"query", "time"}  # type: ignore[arg-type]
    assert set(QueryRange().spec().parameters["properties"]) == {"query", "start", "end", "step"}  # type: ignore[arg-type]


def test_timeout_is_configurable_and_sane() -> None:
    assert Query()._timeout == 15.0
    assert Query(timeout=2.5)._timeout == 2.5


def test_helpers_are_not_exported_as_implements() -> None:
    names = {type(i).__name__ for i in implements()}
    assert names == {"Query", "QueryRange"}


def test_the_default_registry_can_run_it() -> None:
    # Smoke: the registry's own instance works the same way as a test-built one.
    implement = default_registry().get("prometheus.query")
    assert isinstance(implement, Query)
    assert isinstance(implement._transport, type(None))
