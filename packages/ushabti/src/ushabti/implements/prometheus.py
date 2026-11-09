"""Prometheus implements. Scaffold stubs: the operations are declared, not built.

Real versions need the ``prometheus`` extra: pip install "ushabti[prometheus]"
"""

from collections.abc import Mapping
from typing import ClassVar

from pydantic import JsonValue

from ushabti.field import Field
from ushabti.implements import Implement, ImplementKind

_QUERY: dict[str, JsonValue] = {"type": "string", "description": "PromQL expression to evaluate."}


class Query(Implement):
    name: ClassVar[str] = "prometheus.query"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Evaluate a PromQL expression at a single point in time."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {"query": _QUERY},
        "required": ["query"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): send an instant query to the field's Prometheus endpoint over HTTP.
        raise NotImplementedError(
            "prometheus.query is a scaffold stub: the instant query over HTTP "
            "(httpx, the `prometheus` extra) is not implemented yet"
        )


class QueryRange(Implement):
    name: ClassVar[str] = "prometheus.query_range"
    kind: ClassVar[ImplementKind] = ImplementKind.READ
    description: ClassVar[str] = "Evaluate a PromQL expression over a time range."
    parameters: ClassVar[Mapping[str, JsonValue]] = {
        "type": "object",
        "properties": {
            "query": _QUERY,
            "start": {"type": "string", "description": "Start of the range, RFC 3339."},
            "end": {"type": "string", "description": "End of the range, RFC 3339."},
            "step": {"type": "string", "description": "Resolution step, such as 60s."},
        },
        "required": ["query", "start", "end", "step"],
        "additionalProperties": False,
    }

    async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
        # TODO(scaffold): send a range query to the field's Prometheus endpoint over HTTP.
        raise NotImplementedError(
            "prometheus.query_range is a scaffold stub: the range query over HTTP "
            "(httpx, the `prometheus` extra) is not implemented yet"
        )


def implements() -> tuple[Implement, ...]:
    return (Query(), QueryRange())
