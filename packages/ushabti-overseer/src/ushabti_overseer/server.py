"""The HTTP API of the overseer."""

import asyncio
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import httpx
import pydantic
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, PositiveFloat, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ushabti import HereAmI, LogFormat, Summons, SummonsSource, SummonsState
from ushabti_overseer import __version__
from ushabti_overseer.decade import DEFAULT_DECADE_SIZE, Decade
from ushabti_overseer.registry import (
    DEFAULT_MISSED_LIMIT,
    UshabtiStatus,
    WorkforceMember,
    WorkforceRegistry,
)
from ushabti_overseer.routing import NoRouteError, Router

_log = structlog.get_logger(__name__)


class OverseerSettings(BaseSettings):
    """Settings for the overseer. Keys map to ``USHABTI_OVERSEER_<KEY>``.

    The admin token and the logging keys are shared with every ushabti, so
    they keep their ``USHABTI_`` names.
    """

    model_config = SettingsConfigDict(
        env_prefix="USHABTI_OVERSEER_",
        env_ignore_empty=True,
        extra="ignore",
        use_attribute_docstrings=True,
    )

    host: str = "127.0.0.1"
    """Address the HTTP server binds to."""

    port: int = 8081
    """Port the HTTP server listens on."""

    decade_size: PositiveInt = DEFAULT_DECADE_SIZE
    """How many ushabti make a decade."""

    here_am_i_interval: PositiveFloat = 30.0
    """Seconds between the "Here am I" messages the overseer expects from each ushabti."""

    missed_limit: PositiveInt = DEFAULT_MISSED_LIMIT
    """How many expected messages may be missed before an ushabti is marked lost."""

    admin_token: SecretStr | None = pydantic.Field(
        default=None, validation_alias="USHABTI_ADMIN_TOKEN"
    )
    """Bearer token every caller must present, and the one sent to each ushabti."""

    log_level: str = pydantic.Field(default="INFO", validation_alias="USHABTI_LOG_LEVEL")
    """Log level name."""

    log_format: LogFormat = pydantic.Field(
        default=LogFormat.CONSOLE, validation_alias="USHABTI_LOG_FORMAT"
    )
    """Log output: `console` or `json`."""

    def admin_token_value(self) -> str | None:
        return None if self.admin_token is None else self.admin_token.get_secret_value()


class Workforce(BaseModel):
    """Everything the overseer supervises."""

    ushabti: tuple[WorkforceMember, ...]
    decades: tuple[Decade, ...]


class SummonsRouted(BaseModel):
    """The reply to a summons that an ushabti accepted."""

    id: str
    ushabti: str
    decade: int


class HaltReport(BaseModel):
    """The result of fanning a halt out to the workforce."""

    name: str
    halted: bool
    changed: bool
    ushabti: tuple[str, ...]


class Health(BaseModel):
    status: str
    version: str
    ushabti: int
    lost: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def create_app(
    registry: WorkforceRegistry,
    *,
    admin_token: str | None,
    client: httpx.AsyncClient | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> FastAPI:
    """Build the overseer's API over a workforce registry."""
    router = Router()
    http: dict[str, httpx.AsyncClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        http["client"] = client or httpx.AsyncClient(timeout=10.0)
        try:
            yield
        finally:
            await http["client"].aclose()

    app = FastAPI(
        title="ushabti-overseer",
        summary="The overseer: registers ushabti, routes summonses, and tracks their health.",
        version=__version__,
        lifespan=lifespan,
    )

    def require_admin(authorization: Annotated[str | None, Header()] = None) -> None:
        if admin_token is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                "this endpoint is disabled because USHABTI_ADMIN_TOKEN is not set",
            )
        scheme, _, given = (authorization or "").partition(" ")
        matches = secrets.compare_digest(given.strip().encode(), admin_token.encode())
        if scheme.lower() != "bearer" or not matches:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                "a valid bearer token is required",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def bearer() -> dict[str, str]:
        return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

    @app.get("/healthz")
    async def healthz() -> Health:
        """Whether the overseer is up, and how much of the workforce is answering."""
        members = registry.members(clock())
        lost = sum(1 for member in members if member.status is UshabtiStatus.LOST)
        return Health(status="ok", version=__version__, ushabti=len(members), lost=lost)

    # Registration is guarded. The overseer later sends the admin token to the
    # URL an ushabti registers, so it must know that URL came from a peer that
    # already holds the token.
    @app.post("/here-am-i", dependencies=[Depends(require_admin)])
    async def here_am_i(message: HereAmI) -> WorkforceMember:
        """Register an ushabti, or note that a known one answered again."""
        return registry.record(message, clock())

    @app.get("/workforce", dependencies=[Depends(require_admin)])
    async def workforce() -> Workforce:
        """Every ushabti with its decade, spell, status, and last "Here am I"."""
        return Workforce(ushabti=registry.members(clock()), decades=registry.decades())

    @app.post(
        "/summons",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_admin)],
    )
    async def route_summons(summons: Summons) -> SummonsRouted:
        """Route a summons to an ushabti whose spell covers its field and implement."""
        try:
            candidates = router.candidates(summons, registry.members(clock()))
        except NoRouteError as error:
            raise HTTPException(status.HTTP_409_CONFLICT, error.reason) from None

        forwarded = summons.model_copy(
            update={
                "source": SummonsSource.OVERSEER,
                "state": SummonsState.PENDING,
                "detail": None,
            }
        )
        refusals: list[str] = []
        for member in candidates:
            problem = await _post(
                http["client"],
                f"{member.url}/summons",
                bearer(),
                forwarded.model_dump_json(),
                expect=status.HTTP_202_ACCEPTED,
            )
            if problem is None:
                router.delivered(member)
                _log.info("summons_routed", summons=summons.id, ushabti=member.name)
                return SummonsRouted(id=summons.id, ushabti=member.name, decade=member.decade)
            refusals.append(f"{member.name}: {problem}")
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "no ushabti that covers it accepted the summons: " + "; ".join(refusals),
        )

    @app.post("/halt", dependencies=[Depends(require_admin)])
    async def halt() -> HaltReport:
        """Halt the whole workforce. Every ushabti is told, lost ones included."""
        members = registry.members(clock())
        reachable = [member for member in members if member.url]
        problems = await asyncio.gather(
            *(
                _post(http["client"], f"{m.url}/halt", bearer(), None, expect=status.HTTP_200_OK)
                for m in reachable
            )
        )
        failed = {m.name: p for m, p in zip(reachable, problems, strict=True) if p is not None}
        failed.update({m.name: "advertises no URL" for m in members if not m.url})
        halted = tuple(m.name for m in reachable if m.name not in failed)
        _log.warning("halt_fanned_out", halted=list(halted), failed=failed)
        if failed:
            # The caller must not walk away thinking the workforce is halted.
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                {
                    "message": "the halt did not reach every ushabti",
                    "halted": list(halted),
                    "failed": failed,
                },
            )
        return HaltReport(name="overseer", halted=True, changed=bool(halted), ushabti=halted)

    return app


async def _post(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: str | None,
    *,
    expect: int,
) -> str | None:
    """POST to an ushabti. Returns None on success, or what went wrong."""
    try:
        response = await client.post(url, content=body, headers=headers)
    except httpx.HTTPError as error:
        return f"unreachable ({type(error).__name__})"
    if response.status_code != expect:
        return f"answered {response.status_code}"
    return None
