"""Prometheus implements: instant and range queries over the HTTP API.

Both are read implements. They need the ``prometheus`` extra, which is httpx:
pip install "ushabti[prometheus]". The extra is imported only when a query
runs, so the core imports without it.

The field (the target) names the server::

    fields:
      - name: homelab-metrics
        kind: prometheus
        endpoint: ${PROMETHEUS_URL}

The endpoint is resolved when the implement runs, so it may be an environment
reference. It is never written to Papyrus: an observation holds the query and
the result, nothing about where they came from.

Results are shaped for Ma'at conditions. An instant query that returns one
sample exposes it at ``value``, so a spell can write
``expect: {path: value, op: lt, value: 90}``. A range query with one series
exposes ``last``, ``min``, and ``max``. ``NaN`` and infinities become ``null``,
which Ma'at treats as unknown rather than as drift.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Final, cast

from pydantic import JsonValue

from ushabti.errors import ImplementError, MissingExtraError, UnresolvedEnvError
from ushabti.field import Field, FieldKind
from ushabti.implements import Implement, ImplementKind, optional_text_argument, text_argument
from ushabti.spell import expand_env

if TYPE_CHECKING:
    import httpx

TIMEOUT_SECONDS: Final = 15.0
_REASON_LIMIT: Final = 300

_QUERY: dict[str, JsonValue] = {"type": "string", "description": "PromQL expression to evaluate."}
_TIME: dict[str, JsonValue] = {
    "type": "string",
    "description": "Evaluation time, RFC 3339 or Unix seconds. Defaults to now.",
}


class _PrometheusImplement(Implement):
    """What the two queries share: find the endpoint, call it, shape the errors."""

    kind: ClassVar[ImplementKind] = ImplementKind.READ
    path: ClassVar[str]

    def __init__(
        self,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        # A transport is only given by tests, to answer without a network.
        self._transport = transport
        self._timeout = timeout

    async def _call(self, field: Field | None, params: dict[str, str]) -> dict[str, Any]:
        base_url = _endpoint(self.name, field)
        try:
            import httpx
        except ImportError as error:
            raise MissingExtraError("prometheus", "httpx") from error
        try:
            async with httpx.AsyncClient(
                base_url=base_url, timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(self.path, params=params)
        except httpx.HTTPError as error:
            # The error text can carry the URL, which can carry credentials.
            raise ImplementError(
                f"{self.name}: could not reach Prometheus ({type(error).__name__})"
            ) from error
        return _data(self.name, response)


class Query(_PrometheusImplement):
    """An instant query: the value of an expression at one point in time."""

    name: ClassVar[str] = "prometheus.query"
    description: ClassVar[str] = (
        "Evaluate a PromQL expression at a single point in time. Returns the samples "
        "with their labels, their count, and, when there is exactly one sample, its "
        "number at `value`."
    )
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"query": _QUERY, "time": _TIME},
        "required": ["query"],
        "additionalProperties": False,
    }
    path: ClassVar[str] = "/api/v1/query"

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        query = text_argument(self.name, arguments, "query")
        params = {"query": query}
        at = optional_text_argument(self.name, arguments, "time")
        if at is not None:
            params["time"] = at
        data = await self._call(field, params)
        return _shape(self.name, query, data)


class QueryRange(_PrometheusImplement):
    """A range query: the values of an expression over a window of time."""

    name: ClassVar[str] = "prometheus.query_range"
    description: ClassVar[str] = (
        "Evaluate a PromQL expression over a time range. Returns one series per label "
        "set, each with its values and its first, last, min, and max, and, when there "
        "is exactly one series, those four numbers at the top level."
    )
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {
            "query": _QUERY,
            "start": {
                "type": "string",
                "description": "Start of the range, RFC 3339 or Unix seconds.",
            },
            "end": {"type": "string", "description": "End of the range, RFC 3339 or Unix seconds."},
            "step": {"type": "string", "description": "Resolution step, such as 60s, or seconds."},
        },
        "required": ["query", "start", "end", "step"],
        "additionalProperties": False,
    }
    path: ClassVar[str] = "/api/v1/query_range"

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        params: dict[str, str] = {}
        for key in ("query", "start", "end", "step"):
            params[key] = text_argument(self.name, arguments, key)
        data = await self._call(field, params)
        return _shape(self.name, params["query"], data)


def implements() -> tuple[Implement, ...]:
    return (Query(), QueryRange())


# Reaching the server


def _endpoint(name: str, field: Field | None) -> str:
    if field is None or field.kind is not FieldKind.PROMETHEUS:
        raise ImplementError(
            f"{name} needs a field of kind prometheus; name one on the assertion or the summons"
        )
    if not field.endpoint:
        raise ImplementError(
            f"{name}: field {field.name} has no endpoint; set it to the Prometheus base URL, "
            "usually as ${PROMETHEUS_URL}"
        )
    try:
        return expand_env(field.endpoint)
    except UnresolvedEnvError as error:
        raise ImplementError(f"{name}: field {field.name}: {error}") from None


def _data(name: str, response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise ImplementError(
            f"{name}: Prometheus answered HTTP {response.status_code} with a body that is not JSON"
        ) from None
    if not isinstance(body, dict):
        raise ImplementError(
            f"{name}: Prometheus answered HTTP {response.status_code} with no object"
        )
    if body.get("status") != "success":
        kind = body.get("errorType") or f"HTTP {response.status_code}"
        reason = str(body.get("error") or "no reason given")[:_REASON_LIMIT]
        raise ImplementError(f"{name}: Prometheus rejected the query ({kind}): {reason}")
    data = body.get("data")
    if not isinstance(data, dict):
        raise ImplementError(f"{name}: Prometheus answered without a data object")
    return data


# Shaping the result


def _shape(name: str, query: str, data: dict[str, Any]) -> dict[str, JsonValue]:
    result_type = data.get("resultType")
    result = data.get("result")
    try:
        if result_type == "vector":
            return _vector(query, result)
        if result_type == "scalar":
            return _scalar(query, result)
        if result_type == "string":
            return _string(query, result)
        if result_type == "matrix":
            return _matrix(query, result)
    except (KeyError, IndexError, TypeError, ValueError):
        raise ImplementError(
            f"{name}: Prometheus returned a {result_type} in an unexpected shape"
        ) from None
    raise ImplementError(f"{name}: Prometheus returned an unknown result type: {result_type!r}")


def _vector(query: str, result: Any) -> dict[str, JsonValue]:
    samples: list[JsonValue] = [
        {
            "metric": dict(item.get("metric", {})),
            "timestamp": float(item["value"][0]),
            "value": _number(item["value"][1]),
        }
        for item in result
    ]
    only = samples[0] if len(samples) == 1 else None
    return {
        "query": query,
        "result_type": "vector",
        "count": len(samples),
        "value": only["value"] if isinstance(only, dict) else None,
        "samples": samples,
    }


def _scalar(query: str, result: Any) -> dict[str, JsonValue]:
    timestamp, text = result
    value = _number(text)
    return {
        "query": query,
        "result_type": "scalar",
        "count": 1,
        "value": value,
        "samples": [{"metric": {}, "timestamp": float(timestamp), "value": value}],
    }


def _string(query: str, result: Any) -> dict[str, JsonValue]:
    timestamp, text = result
    return {
        "query": query,
        "result_type": "string",
        "count": 1,
        "value": None,
        "text": str(text),
        "timestamp": float(timestamp),
    }


def _matrix(query: str, result: Any) -> dict[str, JsonValue]:
    series: list[dict[str, JsonValue]] = []
    for item in result:
        points: list[JsonValue] = [[float(ts), _number(text)] for ts, text in item["values"]]
        numbers = [p[1] for p in points if isinstance(p, list) and isinstance(p[1], float)]
        series.append(
            {
                "metric": dict(item.get("metric", {})),
                "values": points,
                "first": numbers[0] if numbers else None,
                "last": numbers[-1] if numbers else None,
                "min": min(numbers) if numbers else None,
                "max": max(numbers) if numbers else None,
            }
        )
    only = series[0] if len(series) == 1 else None
    return {
        "query": query,
        "result_type": "matrix",
        "count": len(series),
        "first": only["first"] if only else None,
        "last": only["last"] if only else None,
        "min": only["min"] if only else None,
        "max": only["max"] if only else None,
        "series": cast("list[JsonValue]", series),
    }


def _number(text: Any) -> float | None:
    """A sample value as a float, or None for NaN and infinities, which JSON cannot hold."""
    value = float(text)
    return value if math.isfinite(value) else None
