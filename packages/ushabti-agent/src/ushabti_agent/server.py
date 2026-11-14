"""The HTTP API of one ushabti."""

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Final

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from ushabti import HereAmI, PapyrusEvent, Settings, Summons, SummonsSource
from ushabti_agent import __version__
from ushabti_agent.overseer_client import OverseerClient
from ushabti_agent.runtime import Ushabti

READ_LIMIT: Final = 200
"""How many entries or summonses a read returns when the caller sets no limit."""

READ_LIMIT_MAX: Final = 5000
"""The most a single read may return. Papyrus can be long; read it in pages."""

NDJSON: Final = "application/x-ndjson"


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
            client = OverseerClient(settings.overseer_url, token=settings.admin_token_value())
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
                "this endpoint is disabled because USHABTI_ADMIN_TOKEN is not set",
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

    # A summons puts a caller's words in front of the model and can lead to
    # write actions, so it needs the same token as halt and resume.
    @app.post(
        "/summons",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    async def accept_summons(summons: Summons) -> SummonsAccepted:
        """Queue a summons (a unit of work) from an overseer or an operator."""
        if summons.source is SummonsSource.VIGIL:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "only the ushabti's own vigil raises a summons with source vigil",
            )
        return SummonsAccepted(id=ushabti.submit(summons).id)

    @app.get("/summons", dependencies=[Depends(require_admin)])
    async def list_summonses(
        limit: Annotated[int, Query(ge=1, le=READ_LIMIT_MAX)] = READ_LIMIT,
    ) -> list[Summons]:
        """The summonses this ushabti remembers, newest first."""
        return ushabti.summonses(limit=limit)

    @app.get("/summons/{summons_id}", dependencies=[Depends(require_admin)])
    async def read_summons(summons_id: str) -> Summons:
        """The current state of a summons, including whether it was escalated."""
        summons = ushabti.summons(summons_id)
        if summons is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such summons")
        return summons

    # Papyrus holds the model's proposals and every observation, so reading it
    # is guarded like acting. A plain `def`: FastAPI runs it in a thread, so a
    # long file does not stall the vigil.
    @app.get("/papyrus", dependencies=[Depends(require_admin)])
    def read_papyrus(
        event: Annotated[list[PapyrusEvent] | None, Query()] = None,
        since: Annotated[datetime | None, Query()] = None,
        summons: Annotated[str | None, Query()] = None,
        assertion: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=READ_LIMIT_MAX)] = READ_LIMIT,
    ) -> Response:
        """Papyrus (the audit log) as NDJSON: one entry per line, oldest first.

        Filters: `event` (repeatable), `since` (RFC 3339), `summons` and
        `assertion` (ids in the payload). When more entries match than `limit`,
        the newest are returned.
        """
        entries = ushabti.papyrus_entries(
            events=tuple(event or ()),
            since=since,
            summons=summons,
            assertion=assertion,
            limit=limit,
        )
        body = "".join(entry.model_dump_json() + "\n" for entry in entries)
        return Response(body, media_type=NDJSON)

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
