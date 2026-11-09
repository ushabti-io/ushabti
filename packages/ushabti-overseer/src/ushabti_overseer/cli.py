"""The ``ushabti-overseer`` command."""

import structlog
import typer
import uvicorn

from ushabti import configure_logging
from ushabti_overseer import __version__
from ushabti_overseer.registry import InMemoryWorkforceRegistry
from ushabti_overseer.server import OverseerSettings, create_app

app = typer.Typer(
    help="Run the overseer: the optional orchestrator of a workforce of ushabti.",
    no_args_is_help=True,
    add_completion=False,
)

_log = structlog.get_logger(__name__)


@app.command()
def run() -> None:
    """Run the overseer and serve its HTTP API until stopped."""
    settings = OverseerSettings()
    configure_logging(settings.log_level, settings.log_format)
    token = settings.admin_token_value()
    if token is None:
        typer.echo(
            "cannot start: USHABTI_ADMIN_TOKEN is not set. An overseer only talks to "
            "callers and ushabti that present it.",
            err=True,
        )
        raise typer.Exit(1)
    registry = InMemoryWorkforceRegistry(
        decade_size=settings.decade_size,
        here_am_i_interval=settings.here_am_i_interval,
        missed_limit=settings.missed_limit,
    )
    _log.info(
        "overseer_starting",
        decade_size=settings.decade_size,
        here_am_i_interval=settings.here_am_i_interval,
        missed_limit=settings.missed_limit,
    )
    uvicorn.run(
        create_app(registry, admin_token=token),
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"ushabti-overseer {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
