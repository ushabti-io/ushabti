"""Settings from the environment."""

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from pydantic import ValidationError

from ushabti import LogFormat, Mode, Settings, configure_logging

ENV_KEYS = [
    "MODE",
    "NAME",
    "ADMIN_TOKEN",
    "OVERSEER_URL",
    "ADVERTISE_URL",
    "HERE_AM_I_INTERVAL",
    "PAPYRUS_PATH",
    "FAKE_SCRIPT",
    "HOST",
    "PORT",
    "LOG_LEVEL",
    "LOG_FORMAT",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in ENV_KEYS:
        monkeypatch.delenv(f"USHABTI_{key}", raising=False)
    yield
    # configure_logging binds structlog to the captured stderr, which pytest
    # closes after the test. Later tests must not log into it.
    structlog.reset_defaults()


def test_defaults() -> None:
    settings = Settings()
    assert settings.mode is Mode.SOLO
    assert settings.name is None
    assert settings.admin_token is None
    assert settings.admin_token_value() is None
    assert settings.overseer_url is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8080
    assert settings.here_am_i_interval == 30.0
    assert settings.log_format is LogFormat.CONSOLE


def test_values_come_from_ushabti_prefixed_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_NAME", "watcher-1")
    monkeypatch.setenv("USHABTI_PORT", "9000")
    monkeypatch.setenv("USHABTI_PAPYRUS_PATH", "/tmp/elsewhere.jsonl")  # noqa: S108
    monkeypatch.setenv("USHABTI_LOG_FORMAT", "json")
    monkeypatch.setenv("NAME", "ignored-without-the-prefix")
    settings = Settings()
    assert settings.name == "watcher-1"
    assert settings.port == 9000
    assert settings.papyrus_path == Path("/tmp/elsewhere.jsonl")  # noqa: S108
    assert settings.log_format is LogFormat.JSON


def test_the_admin_token_is_masked_until_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", "test-admin-token")
    settings = Settings()
    assert settings.admin_token_value() == "test-admin-token"
    assert "test-admin-token" not in repr(settings)
    assert "test-admin-token" not in settings.model_dump_json()


def test_an_empty_admin_token_counts_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", "")
    assert Settings().admin_token_value() is None


def test_overseen_mode_needs_an_overseer_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_MODE", "overseen")
    with pytest.raises(ValidationError, match="requires USHABTI_OVERSEER_URL"):
        Settings()
    monkeypatch.setenv("USHABTI_OVERSEER_URL", "http://overseer.example.invalid:8081")
    with pytest.raises(ValidationError, match="requires USHABTI_ADMIN_TOKEN"):
        Settings()
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", "test-admin-token")
    assert Settings().mode is Mode.OVERSEEN


def test_an_overseer_url_needs_overseen_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_OVERSEER_URL", "http://overseer.example.invalid:8081")
    with pytest.raises(ValidationError, match="USHABTI_MODE must be overseen"):
        Settings()


def test_modes_match_the_glossary() -> None:
    assert [mode.value for mode in Mode] == ["solo", "independent", "overseen"]


@pytest.mark.parametrize("log_format", list(LogFormat))
def test_logs_go_to_stderr(log_format: LogFormat, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", log_format)
    log = structlog.get_logger("test")
    log.info("vigil_tick", tick=1)
    log.debug("hidden_below_the_threshold")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "vigil_tick" in captured.err
    assert "hidden_below_the_threshold" not in captured.err


def test_an_unknown_log_level_falls_back_to_info(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("CHATTY")
    structlog.get_logger("test").info("still_logged")
    assert "still_logged" in capsys.readouterr().err
