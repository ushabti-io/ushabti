"""The overseer's HTTP API."""

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import typer
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import ushabti_overseer
from overseer_testkit import AUTHED, BEARER, JSON, TOKEN, Clock, here_am_i, summons
from ushabti_overseer import cli
from ushabti_overseer.registry import InMemoryWorkforceRegistry
from ushabti_overseer.server import OverseerSettings, create_app

Handler = Callable[[httpx.Request], httpx.Response]


class Agents:
    """Stands in for every ushabti the overseer calls, and records the calls."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, int | Exception] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.get(request.url.host, 0)
        if isinstance(answer, Exception):
            raise answer
        if answer:
            return httpx.Response(answer, json={"detail": "refused"})
        if request.url.path == "/summons":
            return httpx.Response(202, json={"id": json.loads(request.content)["id"]})
        return httpx.Response(200, json={"name": request.url.host, "halted": True, "changed": True})

    def calls(self) -> list[tuple[str, str]]:
        return [(request.url.host.split(".")[0], request.url.path) for request in self.requests]


class Overseer:
    def __init__(self, *, token: str | None = TOKEN) -> None:
        self.clock = Clock()
        self.agents = Agents()
        self.registry = InMemoryWorkforceRegistry(decade_size=2, here_am_i_interval=30.0)
        self.app = create_app(
            self.registry,
            admin_token=token,
            client=httpx.AsyncClient(transport=httpx.MockTransport(self.agents)),
            clock=self.clock,
        )


@pytest.fixture
def overseer() -> Overseer:
    return Overseer()


@pytest.fixture
def client(overseer: Overseer) -> Iterator[TestClient]:
    with TestClient(overseer.app, headers=AUTHED) as test_client:
        yield test_client


def register(client: TestClient, name: str, **kwargs: Any) -> dict[str, Any]:
    response = client.post("/here-am-i", content=here_am_i(name, **kwargs).model_dump_json())
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# Registration and the workforce


def test_here_am_i_registers_an_ushabti(client: TestClient) -> None:
    member = register(client, "k3s-watcher")
    assert member["name"] == "k3s-watcher"
    assert member["decade"] == 1
    assert member["status"] == "active"
    assert member["last_here_am_i"] == "2026-01-02T03:04:05Z"


def test_workforce_lists_every_ushabti_with_decade_spell_status_and_last_here_am_i(
    client: TestClient, overseer: Overseer
) -> None:
    for name in ("a", "b", "c"):
        register(client, name)
    overseer.clock.advance(120)
    register(client, "b", halted=True)

    body = client.get("/workforce").json()

    assert [
        (m["name"], m["decade"], m["spell"], m["status"], m["last_here_am_i"])
        for m in body["ushabti"]
    ] == [
        ("a", 1, "a-spell", "lost", "2026-01-02T03:04:05Z"),
        ("b", 1, "b-spell", "halted", "2026-01-02T03:06:05Z"),
        ("c", 2, "c-spell", "lost", "2026-01-02T03:04:05Z"),
    ]
    assert body["decades"] == [
        {"number": 1, "members": ["a", "b"]},
        {"number": 2, "members": ["c"]},
    ]


def test_an_empty_workforce(client: TestClient) -> None:
    assert client.get("/workforce").json() == {"ushabti": [], "decades": []}


def test_healthz_needs_no_token_and_counts_the_workforce(
    client: TestClient, overseer: Overseer
) -> None:
    register(client, "a")
    overseer.clock.advance(3600)
    register(client, "b")
    response = client.get("/healthz", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": ushabti_overseer.__version__,
        "ushabti": 2,
        "lost": 1,
    }


# Routing


def test_a_summons_is_routed_to_the_ushabti_that_covers_it(
    client: TestClient, overseer: Overseer
) -> None:
    register(client, "k3s-watcher")
    register(
        client, "proxmox-watcher", fields=("homelab-proxmox",), implements=("proxmox.start_vm",)
    )

    response = client.post("/summons", content=summons().model_dump_json())

    assert response.status_code == 202
    assert response.json() == {"id": "summons-1", "ushabti": "k3s-watcher", "decade": 1}
    (request,) = overseer.agents.requests
    assert str(request.url) == "http://k3s-watcher.example.invalid:8080/summons"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    forwarded = json.loads(request.content)
    assert forwarded["id"] == "summons-1", "the id survives routing"
    assert forwarded["source"] == "overseer"
    assert forwarded["state"] == "pending"
    assert forwarded["field"] == "homelab-k3s"
    assert forwarded["reason"] == "Restart the web deployment."


def test_summonses_spread_across_ushabti_that_can_take_them(
    client: TestClient, overseer: Overseer
) -> None:
    register(client, "a")
    register(client, "b")
    routed = [
        client.post("/summons", content=summons(summons_id=f"s-{n}").model_dump_json()).json()[
            "ushabti"
        ]
        for n in range(4)
    ]
    assert routed == ["a", "b", "a", "b"]


def test_no_ushabti_to_take_it_is_409_with_the_reason(
    client: TestClient, overseer: Overseer
) -> None:
    empty = client.post("/summons", content=summons().model_dump_json())
    assert empty.status_code == 409
    assert empty.json() == {"detail": "no ushabti is registered"}

    register(client, "k3s-watcher")
    elsewhere = client.post("/summons", content=summons("homelab-proxmox", None).model_dump_json())
    assert elsewhere.status_code == 409
    assert elsewhere.json() == {"detail": "no ushabti tends the field homelab-proxmox"}

    overseer.clock.advance(3600)
    lost = client.post("/summons", content=summons().model_dump_json())
    assert lost.status_code == 409
    assert lost.json() == {
        "detail": "every ushabti that covers it is unavailable: k3s-watcher is lost"
    }
    assert overseer.agents.requests == []


def test_when_one_ushabti_refuses_the_next_is_tried(client: TestClient, overseer: Overseer) -> None:
    register(client, "a")
    register(client, "b")
    overseer.agents.answers["a.example.invalid"] = httpx.ConnectError("refused")

    response = client.post("/summons", content=summons().model_dump_json())

    assert response.status_code == 202
    assert response.json()["ushabti"] == "b"
    assert overseer.agents.calls() == [("a", "/summons"), ("b", "/summons")]


def test_when_every_ushabti_refuses_it_is_409_with_each_reason(
    client: TestClient, overseer: Overseer
) -> None:
    register(client, "a")
    register(client, "b")
    overseer.agents.answers["a.example.invalid"] = httpx.ConnectError("refused")
    overseer.agents.answers["b.example.invalid"] = 404

    response = client.post("/summons", content=summons().model_dump_json())

    assert response.status_code == 409
    assert response.json() == {
        "detail": (
            "no ushabti that covers it accepted the summons: "
            "a: unreachable (ConnectError); b: answered 404"
        )
    }


def test_what_is_not_a_summons_is_422(client: TestClient) -> None:
    assert client.post("/summons", json={"reason": "no source"}).status_code == 422


# Halt


def test_halt_fans_out_to_the_whole_workforce_with_the_token(
    client: TestClient, overseer: Overseer
) -> None:
    for name in ("a", "b", "c"):
        register(client, name)
    overseer.clock.advance(3600)  # all lost, and still told to halt

    response = client.post("/halt")

    assert response.status_code == 200
    assert response.json() == {
        "name": "overseer",
        "halted": True,
        "changed": True,
        "ushabti": ["a", "b", "c"],
    }
    assert sorted(overseer.agents.calls()) == [("a", "/halt"), ("b", "/halt"), ("c", "/halt")]
    assert {r.headers["authorization"] for r in overseer.agents.requests} == {f"Bearer {TOKEN}"}


def test_a_halt_that_misses_someone_is_an_error_that_names_them(
    client: TestClient, overseer: Overseer
) -> None:
    register(client, "a")
    register(client, "b")
    register(client, "c", url="")
    overseer.agents.answers["b.example.invalid"] = 401

    response = client.post("/halt")

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "message": "the halt did not reach every ushabti",
            "halted": ["a"],
            "failed": {"b": "answered 401", "c": "advertises no URL"},
        }
    }


def test_halting_an_empty_workforce_changes_nothing(client: TestClient) -> None:
    assert client.post("/halt").json() == {
        "name": "overseer",
        "halted": True,
        "changed": False,
        "ushabti": [],
    }


# The token


GUARDED = [("POST", "/here-am-i"), ("GET", "/workforce"), ("POST", "/summons"), ("POST", "/halt")]


def body_for(path: str) -> str | None:
    if path == "/here-am-i":
        return here_am_i("intruder", url="http://attacker.example.invalid").model_dump_json()
    if path == "/summons":
        return summons().model_dump_json()
    return None


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_everything_but_healthz_needs_the_bearer_token(
    overseer: Overseer, method: str, path: str
) -> None:
    with TestClient(overseer.app, headers=JSON) as anonymous:
        for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TOKEN}"}):
            response = anonymous.request(method, path, content=body_for(path), headers=headers)
            assert response.status_code == 401, headers
            assert response.headers["www-authenticate"] == "Bearer"
    assert overseer.registry.members(overseer.clock()) == ()
    assert overseer.agents.requests == []


def test_an_unauthenticated_caller_cannot_register_a_url_to_be_sent_the_token(
    overseer: Overseer,
) -> None:
    with TestClient(overseer.app, headers=JSON) as anonymous:
        anonymous.post("/here-am-i", content=body_for("/here-am-i"))
    with TestClient(overseer.app, headers=AUTHED) as operator:
        assert operator.post("/halt").json()["ushabti"] == []
    assert overseer.agents.requests == [], "the token went nowhere"


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_without_a_token_everything_but_healthz_is_disabled(method: str, path: str) -> None:
    overseer = Overseer(token=None)
    with TestClient(overseer.app, headers=JSON) as client:
        for headers in ({}, BEARER):
            response = client.request(method, path, content=body_for(path), headers=headers)
            assert response.status_code == 404
            assert "USHABTI_ADMIN_TOKEN is not set" in response.json()["detail"]
        assert client.get("/healthz").status_code == 200


# Settings and the command


def test_overseer_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("USHABTI_ADMIN_TOKEN", "USHABTI_LOG_LEVEL", "USHABTI_LOG_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    defaults = OverseerSettings()
    assert (defaults.host, defaults.port) == ("127.0.0.1", 8081)
    assert (defaults.decade_size, defaults.missed_limit) == (10, 3)
    assert defaults.here_am_i_interval == 30.0
    assert defaults.admin_token_value() is None

    monkeypatch.setenv("USHABTI_OVERSEER_DECADE_SIZE", "36")
    monkeypatch.setenv("USHABTI_OVERSEER_MISSED_LIMIT", "5")
    monkeypatch.setenv("USHABTI_OVERSEER_PORT", "9081")
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("USHABTI_LOG_FORMAT", "json")
    settings = OverseerSettings()
    assert (settings.decade_size, settings.missed_limit, settings.port) == (36, 5, 9081)
    assert settings.admin_token_value() == TOKEN
    assert settings.log_format.value == "json"
    assert TOKEN not in repr(settings)


def test_the_command_refuses_to_run_without_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USHABTI_ADMIN_TOKEN", raising=False)
    result = CliRunner().invoke(cli.app, ["run"])
    assert result.exit_code == 1
    assert "USHABTI_ADMIN_TOKEN is not set" in result.stderr


def test_the_command_serves_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        served.update(kwargs, app=app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("USHABTI_OVERSEER_PORT", "9081")
    monkeypatch.setenv("USHABTI_OVERSEER_DECADE_SIZE", "1")

    result = CliRunner().invoke(cli.app, ["run"])

    assert result.exit_code == 0, result.output
    assert (served["host"], served["port"]) == ("127.0.0.1", 9081)
    with TestClient(served["app"], headers=AUTHED) as client:
        assert register(client, "a")["decade"] == 1
        assert register(client, "b")["decade"] == 2, "the configured decade size is used"


def test_the_version_command() -> None:
    result = CliRunner().invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"ushabti-overseer {ushabti_overseer.__version__}"
    assert isinstance(cli.app, typer.Typer)
