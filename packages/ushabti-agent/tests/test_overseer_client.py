"""Reporting to an overseer."""

import asyncio
import json
from pathlib import Path

import httpx

from agent_testkit import World
from ushabti import HereAmI, Settings
from ushabti_agent.overseer_client import OverseerClient
from ushabti_agent.server import create_app

OVERSEER = "http://overseer.example.invalid:8081"
TOKEN = "test-admin-token"


def client_answering(handler: httpx.MockTransport, token: str | None = TOKEN) -> OverseerClient:
    return OverseerClient(f"{OVERSEER}/", token=token, client=httpx.AsyncClient(transport=handler))


async def test_here_am_i_is_posted_as_json(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"name": "test-spell"})

    client = client_answering(httpx.MockTransport(handler))
    message = World(tmp_path).ushabti.here_am_i()

    assert await client.here_am_i(message) is True
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{OVERSEER}/here-am-i"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert HereAmI.model_validate(json.loads(request.content)) == message
    await client.close()


async def test_without_a_token_no_authorization_header_is_sent(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(404)

    client = client_answering(httpx.MockTransport(handler), token=None)
    assert await client.here_am_i(World(tmp_path).ushabti.here_am_i()) is False
    assert "authorization" not in seen[0].headers


async def test_an_overseer_that_refuses_is_reported_not_raised(tmp_path: Path) -> None:
    client = client_answering(httpx.MockTransport(lambda request: httpx.Response(503)))
    assert await client.here_am_i(World(tmp_path).ushabti.here_am_i()) is False


async def test_an_unreachable_overseer_is_reported_not_raised(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nobody is listening", request=request)

    client = client_answering(httpx.MockTransport(handler))
    assert await client.here_am_i(World(tmp_path).ushabti.here_am_i()) is False


async def test_run_registers_at_once_and_repeats_until_stopped(tmp_path: Path) -> None:
    seen: list[HereAmI] = []
    stop = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(HereAmI.model_validate(json.loads(request.content)))
        if len(seen) == 3:
            stop.set()
        return httpx.Response(200, json={})

    client = client_answering(httpx.MockTransport(handler))
    world = World(tmp_path)
    await asyncio.wait_for(client.run(world.ushabti, 0.01, stop), timeout=5)

    assert len(seen) == 3
    assert {message.name for message in seen} == {"test-spell"}


async def test_the_app_reports_to_its_overseer_while_it_runs(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={})

    overseer = client_answering(httpx.MockTransport(handler))
    world = World(tmp_path)
    app = create_app(world.ushabti, Settings(), overseer=overseer)

    async with app.router.lifespan_context(app):
        async with asyncio.timeout(5):
            while not seen:
                await asyncio.sleep(0.005)

    assert seen[0] == "/here-am-i"
