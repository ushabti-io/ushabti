"""Process settings, read from environment variables with the USHABTI_ prefix."""

import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import Self

import structlog
from pydantic import PositiveFloat, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(StrEnum):
    """The topology an ushabti runs in."""

    SOLO = "solo"
    INDEPENDENT = "independent"
    OVERSEEN = "overseen"


class LogFormat(StrEnum):
    CONSOLE = "console"
    JSON = "json"


class Settings(BaseSettings):
    """Settings for one process. Every key maps to ``USHABTI_<KEY>``.

    Secrets live here and nowhere else: they come from the environment, never
    from a spell or any other file.
    """

    model_config = SettingsConfigDict(
        env_prefix="USHABTI_",
        env_ignore_empty=True,
        extra="ignore",
        use_attribute_docstrings=True,
    )

    mode: Mode = Mode.SOLO
    """Topology: `solo`, `independent`, or `overseen`."""

    name: str | None = None
    """Name of this ushabti. Defaults to the name of its spell."""

    admin_token: SecretStr | None = None
    """Bearer token for the halt and resume endpoints. Unset disables them."""

    overseer_url: str | None = None
    """Base URL of the overseer. Required in `overseen` mode."""

    advertise_url: str | None = None
    """URL where the overseer can reach this ushabti."""

    here_am_i_interval: PositiveFloat = 30.0
    """Seconds between "Here am I" messages to the overseer."""

    papyrus_path: Path | None = None
    """Overrides the Papyrus path the spell declares."""

    fake_script: Path | None = None
    """YAML file of scripted completions for the `fake` model provider."""

    host: str = "127.0.0.1"
    """Address the HTTP server binds to."""

    port: int = 8080
    """Port the HTTP server listens on."""

    log_level: str = "INFO"
    """Log level name, such as `INFO` or `DEBUG`."""

    log_format: LogFormat = LogFormat.CONSOLE
    """Log output: `console` for people, `json` for log shippers."""

    @model_validator(mode="after")
    def _mode_matches_overseer(self) -> Self:
        if self.mode is Mode.OVERSEEN and self.overseer_url is None:
            raise ValueError("USHABTI_MODE=overseen requires USHABTI_OVERSEER_URL")
        if self.overseer_url is not None and self.mode is not Mode.OVERSEEN:
            raise ValueError("USHABTI_OVERSEER_URL is set, so USHABTI_MODE must be overseen")
        return self

    def admin_token_value(self) -> str | None:
        """The admin token in the clear, or None when the endpoints are disabled."""
        return None if self.admin_token is None else self.admin_token.get_secret_value()


class _StderrLogger:
    """Writes each log line to whatever ``sys.stderr`` is at that moment.

    Looking the stream up on every call, and not once at setup, keeps logging
    working after stderr is redirected or replaced.
    """

    def msg(self, message: str) -> None:
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

    log = debug = info = warn = warning = msg
    fatal = failure = err = error = critical = exception = msg


def configure_logging(level: str = "INFO", log_format: LogFormat = LogFormat.CONSOLE) -> None:
    """Send structured logs to stderr, leaving stdout for command output."""
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if log_format is LogFormat.JSON
        else structlog.dev.ConsoleRenderer()
    )
    threshold = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(threshold),
        logger_factory=lambda *args: _StderrLogger(),
        cache_logger_on_first_use=False,
    )
