"""The HTTP API of one ushabti."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from ushabti import HereAmI, Settings, Summons, SummonsSource
from ushabti_agent import __version__
from ushabti_agent.overseer_client import OverseerClient
from ushabti_agent.runtime import Ushabti


class SummonsAccepted(BaseModel):
    """The reply to an accepted summons."""

    id: str


class HaltState(BaseModel):
    """The reply to a halt or resume request."""

    name: str
    halted: bool
    changed: bool


def create_app(
    ushabti: Ushabti,
    settings: Settings,
    *,
    background: bool = True,
    overseer: OverseerClient | None = None,
) -> FastAPI:
    """Build the API for an ushabti.

    With ``background`` set, the app keeps the vigil for as long as it runs,
    and reports to the overseer when one is configured. Tests turn it off and
    drive the ushabti themselves.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not background:
            yield
            return
        stop = asyncio.Event()
        running = [asyncio.ensure_future(ushabti.run(stop))]
        client = overseer
        if client is None and settings.overseer_url is not None:
            client = OverseerClient(settings.overseer_url)
        if client is not None:
            running.append(
                asyncio.ensure_future(client.run(ushabti, settings.here_am_i_interval, stop))
            )
        try:
            yield
        finally:
            stop.set()
            await asyncio.gather(*running, return_exceptions=True)
            if client is not None:
                await client.close()

    app = FastAPI(
        title="ushabti",
        summary="One ushabti (one agent instance) running one spell.",
        version=__version__,
        lifespan=lifespan,
    )

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        token = settings.admin_token_value()
        if token is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "halt and resume are disabled because USHABTI_ADMIN_TOKEN is not set",
            )
        scheme, _, given = (authorization or "").partition(" ")
        matches = secrets.compare_digest(given.strip().encode(), token.encode())
        if scheme.lower() != "bearer" or not matches:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "a valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/healthz")
    async def healthz() -> HereAmI:
        """Here am I: name, spell, mode, halted flag, and last vigil time."""
        return ushabti.here_am_i()

    @app.get("/metrics")
    async def metrics() -> Response:
        """Prometheus metrics."""
        return Response(generate_latest(ushabti.metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/summons", status_code=status.HTTP_202_ACCEPTED)
    async def accept_summons(summons: Summons) -> SummonsAccepted:
        """Queue a summons (a unit of work) from an overseer or an operator."""
        if summons.source is SummonsSource.VIGIL:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "only the ushabti's own vigil raises a summons with source vigil",
            )
        return SummonsAccepted(id=ushabti.submit(summons).id)

    @app.get("/summons/{summons_id}")
    async def read_summons(summons_id: str) -> Summons:
        """The current state of a summons, including whether it was escalated."""
        summons = ushabti.summons(summons_id)
        if summons is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such summons")
        return summons

    @app.post("/halt", dependencies=[Depends(require_admin)])
    async def halt() -> HaltState:
        """Stop acting at once. Needs no model. Observing continues."""
        changed = ushabti.halt(by="api")
        return HaltState(name=ushabti.name, halted=ushabti.halted, changed=changed)

    @app.post("/resume", dependencies=[Depends(require_admin)])
    async def resume() -> HaltState:
        """Act again after a halt."""
        changed = ushabti.resume(by="api")
        return HaltState(name=ushabti.name, halted=ushabti.halted, changed=changed)

    return app
