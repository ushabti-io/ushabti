"""The ushabti command."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import typer
import uvicorn
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import ushabti_agent
from agent_testkit import LOOSE, UNHEALTHY, World, make_spell, write_spell
from ushabti import Settings
from ushabti_agent import cli
from ushabti_agent.server import create_app

REPO = Path(__file__).resolve().parents[3]
EXAMPLES = sorted((REPO / "examples" / "spells").glob("*.yaml"))
SECRET = "sk-live-do-not-leak-0123456789"
TOKEN = "test-admin-token"

runner = CliRunner()


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("USHABTI_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def spell_at(tmp_path: Path, **spec: Any) -> Path:
    spec.setdefault("papyrus", {"path": str(tmp_path / "papyrus.jsonl")})
    return write_spell(tmp_path / "spell.yaml", **spec)


def papyrus_events(path: Path) -> list[str]:
    return [json.loads(line)["event"] for line in path.read_text().splitlines()]


# check-spell


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda path: path.name)
def test_the_example_spells_pass_check_spell(example: Path) -> None:
    result = runner.invoke(cli.app, ["check-spell", str(example)])
    assert result.exit_code == 0, result.stderr
    assert "valid spell" in result.stderr
    assert "dry_run: true" in result.stdout


def test_there_are_example_spells_to_check() -> None:
    assert [path.name for path in EXAMPLES] == ["k3s-watcher.yaml", "proxmox-watcher.yaml"]


def test_check_spell_prints_the_resolved_configuration_with_secrets_masked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USHABTI_MODEL", "test-model")
    monkeypatch.setenv("TEST_API_KEY", SECRET)
    path = spell_at(
        tmp_path,
        model={"provider": "anthropic", "name": "${USHABTI_MODEL}", "api_key": "${TEST_API_KEY}"},
    )
    result = runner.invoke(cli.app, ["check-spell", str(path)])

    assert result.exit_code == 0
    assert "name: test-model" in result.stdout
    assert "api_key: '********'" in result.stdout
    assert SECRET not in result.output
    assert "warning" not in result.stderr


def test_check_spell_warns_about_unset_variables_but_still_passes(tmp_path: Path) -> None:
    path = spell_at(tmp_path, model={"provider": "anthropic", "name": "${NOT_SET_ANYWHERE}"})
    result = runner.invoke(cli.app, ["check-spell", str(path)])
    assert result.exit_code == 0
    assert "name: <unset ${NOT_SET_ANYWHERE}>" in result.stdout
    assert "warning: environment variable NOT_SET_ANYWHERE is not set" in result.stderr


def test_check_spell_rejects_an_inline_secret_without_echoing_it(tmp_path: Path) -> None:
    path = spell_at(tmp_path, model={"provider": "anthropic", "api_key": SECRET})
    result = runner.invoke(cli.app, ["check-spell", str(path)])
    assert result.exit_code == 2
    assert "spec.model.api_key: a secret must be an environment reference" in result.stderr
    assert SECRET not in result.output


def test_check_spell_lists_every_problem(tmp_path: Path) -> None:
    path = spell_at(tmp_path, implements=["noop.read", "shell.exec"], maat=[])
    result = runner.invoke(cli.app, ["check-spell", str(path)])
    assert result.exit_code == 2
    assert "is not a valid spell" in result.stderr
    assert "shell.exec is not a registered implement" in result.stderr
    assert result.stdout == ""


def test_check_spell_reports_a_missing_file(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["check-spell", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 2
    assert "cannot read the file" in result.stderr


# run --once


def test_run_once_does_one_tick_and_exits(tmp_path: Path) -> None:
    path = spell_at(tmp_path)
    result = runner.invoke(cli.app, ["run", "--spell", str(path), "--model", "fake", "--once"])

    assert result.exit_code == 0, result.output
    assert "ushabti test-spell: vigil tick 1 complete" in result.stdout
    assert "mode: solo  model: fake  dry_run: true  halted: false" in result.stdout
    assert "isfet: 1  omens: 0  summons raised: 1  answered: 1" in result.stdout
    assert papyrus_events(tmp_path / "papyrus.jsonl") == [
        "vigil_tick",
        "observation",
        "isfet",
        "summons",
        "proposal",
        "summons",
    ]


def test_run_once_with_a_fake_script_shows_the_weighing_at_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "script.yaml"
    script.write_text(
        "- content: Restarting.\n"
        "  tool_calls:\n"
        "    - name: noop.write\n"
        "      arguments: {action: restart}\n"
        "- content: Done.\n"
    )
    monkeypatch.setenv("USHABTI_FAKE_SCRIPT", str(script))
    path = spell_at(tmp_path)
    result = runner.invoke(cli.app, ["run", "--spell", str(path), "--once"])

    assert result.exit_code == 0, result.output
    assert "answered (verdicts: 1 DRY_RUN)" in result.stdout
    lines = (tmp_path / "papyrus.jsonl").read_text().splitlines()
    verdicts = [json.loads(line) for line in lines if '"event":"verdict"' in line]
    assert [entry["verdict"] for entry in verdicts] == ["DRY_RUN"]


def test_run_once_takes_name_and_papyrus_path_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere" / "log.jsonl"
    monkeypatch.setenv("USHABTI_NAME", "watcher-7")
    monkeypatch.setenv("USHABTI_PAPYRUS_PATH", str(elsewhere))
    result = runner.invoke(cli.app, ["run", "--spell", str(spell_at(tmp_path)), "--once"])

    assert result.exit_code == 0, result.output
    assert "ushabti watcher-7:" in result.stdout
    assert f"papyrus: {elsewhere}" in result.stdout
    assert json.loads(elsewhere.read_text().splitlines()[0])["ushabti"] == "watcher-7"
    assert not (tmp_path / "papyrus.jsonl").exists()


def test_run_once_without_a_usable_model_still_observes(tmp_path: Path) -> None:
    # openai_compatible needs a model name, and this variable is not set.
    path = spell_at(tmp_path, model={"provider": "openai_compatible", "name": "${NOT_SET}"})
    result = runner.invoke(cli.app, ["run", "--spell", str(path), "--once"])

    assert result.exit_code == 0, result.output
    assert "model: none" in result.stdout
    assert "failed (no model is configured)" in result.stdout


def test_run_rejects_an_invalid_spell(tmp_path: Path) -> None:
    path = spell_at(tmp_path, implements=["shell.exec"], maat=[])
    result = runner.invoke(cli.app, ["run", "--spell", str(path), "--once"])
    assert result.exit_code == 2
    assert "shell.exec is not a registered implement" in result.stderr


def test_run_rejects_an_unknown_model_choice(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app, ["run", "--spell", str(spell_at(tmp_path)), "--model", "crystal-ball"]
    )
    assert result.exit_code == 2


def test_run_serves_the_api_when_not_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        served.update(kwargs, app=app)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    monkeypatch.setenv("USHABTI_PORT", "9123")
    result = runner.invoke(cli.app, ["run", "--spell", str(spell_at(tmp_path)), "--model", "fake"])

    assert result.exit_code == 0, result.output
    assert served["host"] == "127.0.0.1"
    assert served["port"] == 9123
    with TestClient(served["app"]) as client:
        assert client.get("/healthz").json()["name"] == "test-spell"


# Where Papyrus goes


def test_the_spell_path_is_used_when_it_is_writable(tmp_path: Path) -> None:
    spell = make_spell(papyrus={"path": str(tmp_path / "deep" / "papyrus.jsonl")})
    assert cli.papyrus_path(spell, Settings()) == tmp_path / "deep" / "papyrus.jsonl"


def test_papyrus_is_never_created_world_readable(tmp_path: Path) -> None:
    path = tmp_path / "papyrus.jsonl"
    result = runner.invoke(
        cli.app, ["run", "--spell", str(spell_at(tmp_path)), "--model", "fake", "--once"]
    )
    assert result.exit_code == 0, result.output
    assert path.stat().st_mode & 0o007 == 0, oct(path.stat().st_mode)


def test_an_unwritable_spell_path_falls_back_to_the_user_state_directory(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where a directory is wanted")
    spell = make_spell(papyrus={"path": str(blocker / "papyrus.jsonl")})

    chosen = cli.papyrus_path(spell, Settings())

    assert chosen == tmp_path / "state" / "ushabti" / "test-spell" / "papyrus.jsonl"
    assert chosen.exists()


def test_the_state_directory_defaults_to_the_home_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("XDG_STATE_HOME")
    assert cli.state_directory() == Path.home() / ".local" / "state" / "ushabti"


def test_an_explicit_papyrus_path_is_never_second_guessed(tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    settings = Settings(papyrus_path=blocker / "papyrus.jsonl")
    with pytest.raises(typer.Exit) as caught:
        cli.papyrus_path(make_spell(), settings)
    assert caught.value.exit_code == 1


def test_nowhere_to_write_papyrus_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    spell = make_spell(papyrus={"path": str(blocker / "papyrus.jsonl")})
    with pytest.raises(typer.Exit):
        cli.papyrus_path(spell, Settings())


# halt, resume, version


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """An ushabti whose API the CLI reaches as if over the network."""
    world = World(tmp_path, spell=make_spell(weighing=LOOSE, maat=[UNHEALTHY]))
    settings = Settings(admin_token=TOKEN)
    client = TestClient(create_app(world.ushabti, settings, background=False))

    def post(url: str, **kwargs: Any) -> Any:
        kwargs.pop("timeout", None)
        return client.post(httpx.URL(url).path, **kwargs)

    monkeypatch.setattr(httpx, "post", post)
    return world


def test_halt_and_resume_reach_the_ushabti(served: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", TOKEN)

    halted = runner.invoke(cli.app, ["halt"])
    assert halted.exit_code == 0, halted.output
    assert halted.stdout.strip() == "test-spell: halted"
    assert served.ushabti.here_am_i().halted

    again = runner.invoke(cli.app, ["halt", "--url", "http://agent.example.invalid:8080/"])
    assert again.stdout.strip() == "test-spell: halted (no change)"

    resumed = runner.invoke(cli.app, ["resume"])
    assert resumed.stdout.strip() == "test-spell: active"
    assert not served.ushabti.here_am_i().halted


def test_halt_needs_the_admin_token_in_the_environment(served: World) -> None:
    result = runner.invoke(cli.app, ["halt"])
    assert result.exit_code == 1
    assert "cannot halt: USHABTI_ADMIN_TOKEN is not set" in result.stderr
    assert not served.ushabti.here_am_i().halted


def test_halt_with_the_wrong_token_reports_the_refusal(
    served: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", "not-the-token")
    result = runner.invoke(cli.app, ["halt"])
    assert result.exit_code == 1
    assert "answered 401: a valid bearer token is required" in result.stderr
    assert not served.ushabti.here_am_i().halted


def test_halt_reports_an_unreachable_ushabti(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, **kwargs: Any) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", refuse)
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", TOKEN)
    result = runner.invoke(cli.app, ["halt"])
    assert result.exit_code == 1
    assert "cannot halt: could not reach http://127.0.0.1:8080" in result.stderr


def test_halt_reports_an_answer_that_is_not_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(502, text="bad gateway"))
    monkeypatch.setenv("USHABTI_ADMIN_TOKEN", TOKEN)
    result = runner.invoke(cli.app, ["resume"])
    assert result.exit_code == 1
    assert "answered 502: bad gateway" in result.stderr


def test_version() -> None:
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == f"ushabti-agent {ushabti_agent.__version__}"


def test_no_arguments_shows_help() -> None:
    result = runner.invoke(cli.app, [])
    assert "check-spell" in result.output
    assert "halt" in result.output
    assert "resume" in result.output
