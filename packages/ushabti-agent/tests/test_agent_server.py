"""The HTTP API of one ushabti."""

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_testkit import LOOSE, World, make_spell, proposing, write_call
from ushabti import HereAmI, Mode, Settings, Summons, SummonsState
from ushabti.models.fake import FakeProvider
from ushabti_agent.server import create_app

TOKEN = "test-admin-token"
BEARER = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("MODE", "NAME", "ADMIN_TOKEN", "OVERSEER_URL", "ADVERTISE_URL", "PAPYRUS_PATH"):
        monkeypatch.delenv(f"USHABTI_{key}", raising=False)


@pytest.fixture
def world(tmp_path: Path) -> World:
    provider = FakeProvider([proposing(write_call())])
    return World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE, maat=[]))


def is_halted(world: World) -> bool:
    return world.ushabti.halted


def client_for(
    world: World, *, token: str | None = TOKEN, authenticated: bool = True
) -> TestClient:
    settings = Settings(admin_token=token)
    app = create_app(world.ushabti, settings, background=False)
    return TestClient(app, headers=BEARER if authenticated else None)


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    with client_for(world) as test_client:
        yield test_client


def test_healthz_returns_a_valid_here_am_i(client: TestClient, world: World) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    message = HereAmI.model_validate(response.json())
    assert message.name == "test-spell"
    assert message.spell == "test-spell"
    assert message.mode is Mode.SOLO
    assert message.halted is False
    assert message.last_vigil is None
    assert set(response.json()) >= {"name", "spell", "mode", "halted", "last_vigil"}


async def test_healthz_reports_the_last_vigil_time(client: TestClient, world: World) -> None:
    await world.ushabti.tick()
    message = HereAmI.model_validate(client.get("/healthz").json())
    assert message.last_vigil == world.clock.now


def test_metrics_exposes_the_promised_series(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for series in (
        "ushabti_vigil_ticks_total 0.0",
        "ushabti_isfet_total 0.0",
        "ushabti_omens_total 0.0",
        'ushabti_verdicts_total{verdict="PASS"} 0.0',
        'ushabti_verdicts_total{verdict="DRY_RUN"} 0.0',
        'ushabti_verdicts_total{verdict="NEEDS_APPROVAL"} 0.0',
        'ushabti_verdicts_total{verdict="REJECT"} 0.0',
        'ushabti_actions_total{kind="write"} 0.0',
        'ushabti_actions_total{kind="read"} 0.0',
        "ushabti_halted 0.0",
    ):
        assert series in body, series


async def test_metrics_count_what_happened(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call())])
    world = World(tmp_path, provider=provider, spell=make_spell(weighing=LOOSE))
    await world.ushabti.once()
    world.ushabti.halt()
    with client_for(world) as client:
        body = client.get("/metrics").text
    assert "ushabti_vigil_ticks_total 1.0" in body
    assert "ushabti_isfet_total 1.0" in body
    assert 'ushabti_verdicts_total{verdict="PASS"} 1.0' in body
    assert 'ushabti_actions_total{kind="write"} 1.0' in body
    assert "ushabti_halted 1.0" in body


async def test_post_summons_returns_202_with_its_id(client: TestClient, world: World) -> None:
    response = client.post("/summons", json={"source": "operator", "reason": "Restart it."})
    assert response.status_code == 202
    summons_id = response.json()["id"]
    assert set(response.json()) == {"id"}

    pending = Summons.model_validate(client.get(f"/summons/{summons_id}").json())
    assert pending.state is SummonsState.PENDING
    assert pending.reason == "Restart it."

    await world.ushabti.answer_pending()
    answered = Summons.model_validate(client.get(f"/summons/{summons_id}").json())
    assert answered.state is SummonsState.ANSWERED
    assert world.write.calls == [{"action": "restart"}]


def test_post_summons_keeps_the_id_an_overseer_chose(client: TestClient) -> None:
    body = {"id": "routed-1", "source": "overseer", "reason": "Check.", "field": "test-field"}
    assert client.post("/summons", json=body).json() == {"id": "routed-1"}
    assert client.post("/summons", json=body).json() == {"id": "routed-1"}


def test_post_summons_rejects_what_is_not_a_summons(client: TestClient) -> None:
    assert client.post("/summons", json={"reason": "no source"}).status_code == 422
    assert client.post("/summons", json={"source": "passerby", "reason": "x"}).status_code == 422
    response = client.post("/summons", json={"source": "vigil", "reason": "forged"})
    assert response.status_code == 422
    assert "only the ushabti's own vigil" in response.json()["detail"]


def test_an_unknown_summons_is_404(client: TestClient) -> None:
    assert client.get("/summons/nope").status_code == 404


def test_an_escalated_summons_is_visible_through_the_api(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call())])
    spell = make_spell(weighing={"dry_run": False, "require_approval_for": ["write"]}, maat=[])
    world = World(tmp_path, provider=provider, spell=spell)
    with client_for(world) as client:
        summons_id = client.post(
            "/summons", json={"source": "operator", "reason": "Restart it."}
        ).json()["id"]
        client.portal.call(world.ushabti.answer_pending)  # type: ignore[union-attr]
        body = client.get(f"/summons/{summons_id}").json()
    assert body["state"] == "escalated"
    assert body["detail"] == "verdicts: 1 NEEDS_APPROVAL"


# Halt and resume


GUARDED = [
    ("POST", "/halt"),
    ("POST", "/resume"),
    ("POST", "/summons"),
    ("GET", "/summons/some-id"),
]


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_everything_that_acts_needs_the_bearer_token(world: World, method: str, path: str) -> None:
    body = {"source": "operator", "reason": "Restart it."} if path == "/summons" else None
    with client_for(world, authenticated=False) as client:
        missing = client.request(method, path, json=body)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        for wrong in ("Bearer wrong", f"Basic {TOKEN}", TOKEN, "Bearer "):
            refused = client.request(method, path, json=body, headers={"Authorization": wrong})
            assert refused.status_code == 401, wrong
    assert not is_halted(world)
    assert world.events() == [], "a refused request leaves no trace and queues nothing"


def test_health_and_metrics_need_no_token(world: World) -> None:
    with client_for(world, authenticated=False) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/metrics").status_code == 200


def test_halt_takes_effect_immediately_and_resume_undoes_it(
    client: TestClient, world: World
) -> None:
    halted = client.post("/halt", headers=BEARER)
    assert halted.status_code == 200
    assert halted.json() == {"name": "test-spell", "halted": True, "changed": True}
    assert is_halted(world)
    assert client.get("/healthz").json()["halted"] is True
    assert client.post("/halt", headers=BEARER).json()["changed"] is False

    resumed = client.post("/resume", headers=BEARER)
    assert resumed.json() == {"name": "test-spell", "halted": False, "changed": True}
    assert not is_halted(world)
    assert [entry.payload for entry in world.of("halt")] == [{"by": "api"}]
    assert world.events() == ["halt", "resume"]


def test_halt_over_http_needs_no_model(tmp_path: Path) -> None:
    world = World(tmp_path, provider=None)
    with client_for(world) as client:
        assert client.post("/halt", headers=BEARER).json()["halted"] is True


@pytest.mark.parametrize(("method", "path"), GUARDED)
def test_without_a_token_everything_that_acts_is_disabled(
    world: World, method: str, path: str
) -> None:
    body = {"source": "operator", "reason": "Restart it."} if path == "/summons" else None
    with client_for(world, token=None, authenticated=False) as client:
        for headers in ({}, BEARER):
            response = client.request(method, path, json=body, headers=headers)
            assert response.status_code == 404
            assert "USHABTI_ADMIN_TOKEN is not set" in response.json()["detail"]
    assert not is_halted(world)
    assert world.events() == []


# The app keeps the vigil while it runs


def test_the_running_app_keeps_the_vigil_and_answers_summonses(tmp_path: Path) -> None:
    provider = FakeProvider([proposing(write_call())])
    spell = make_spell(weighing=LOOSE, maat=[], vigil={"interval": "20ms"})
    world = World(tmp_path, provider=provider, spell=spell)
    app = create_app(world.ushabti, Settings(admin_token=TOKEN))

    with TestClient(app, headers=BEARER) as client:
        summons_id = client.post(
            "/summons", json={"source": "operator", "reason": "Restart it."}
        ).json()["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f"/summons/{summons_id}").json()["state"]
            vigil = client.get("/healthz").json()["last_vigil"]
            if state == "answered" and vigil is not None:
                break
            time.sleep(0.01)
        assert state == "answered"
        assert vigil is not None

    ticks = len(world.of("vigil_tick"))
    assert ticks >= 1
    time.sleep(0.1)
    assert len(world.of("vigil_tick")) == ticks, "the vigil stops when the app stops"
