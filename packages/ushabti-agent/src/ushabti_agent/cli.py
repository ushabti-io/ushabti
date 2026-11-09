"""The ``ushabti`` command."""

import asyncio
import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import httpx
import structlog
import typer
import uvicorn
import yaml

from ushabti import (
    JsonlPapyrus,
    MissingExtraError,
    ModelError,
    ModelProvider,
    ModelProviderName,
    Settings,
    Spell,
    SpellError,
    build_provider,
    configure_logging,
    load_spell,
    resolve_spell,
)
from ushabti_agent import __version__
from ushabti_agent.runtime import TickReport, Ushabti
from ushabti_agent.server import create_app

DEFAULT_URL = "http://127.0.0.1:8080"

app = typer.Typer(
    help="Run and control an ushabti: one agent instance running one spell.",
    no_args_is_help=True,
    add_completion=False,
)

_log = structlog.get_logger(__name__)


class ModelChoice(StrEnum):
    FAKE = "fake"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


class _BlockDumper(yaml.SafeDumper):
    """Writes multi-line strings as YAML blocks, the way a person would."""


def _represent_str(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_BlockDumper.add_representer(str, _represent_str)


def _fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _load(path: Path) -> Spell:
    try:
        return load_spell(path)
    except SpellError as error:
        raise _fail(str(error), 2) from None


def state_directory() -> Path:
    """Where per-user state lives when the system location is not writable."""
    base = os.environ.get("XDG_STATE_HOME")
    return (Path(base) if base else Path.home() / ".local" / "state") / "ushabti"


def _can_append(path: Path) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
    except OSError:
        return False
    return True


def papyrus_path(spell: Spell, settings: Settings) -> Path:
    """Decide where Papyrus (the audit log) is written.

    ``USHABTI_PAPYRUS_PATH`` wins and is never second-guessed. Otherwise the
    spell's path is used. If that path cannot be written, which is normal for a
    system path such as ``/var/lib/ushabti`` on a workstation, the per-user
    state directory is used instead and a warning says so.
    """
    if settings.papyrus_path is not None:
        if not _can_append(settings.papyrus_path):
            raise _fail(f"cannot write Papyrus at {settings.papyrus_path} (USHABTI_PAPYRUS_PATH)")
        return settings.papyrus_path
    declared = spell.spec.papyrus.path
    if _can_append(declared):
        return declared
    fallback = state_directory() / spell.name / "papyrus.jsonl"
    if not _can_append(fallback):
        raise _fail(
            f"cannot write Papyrus at {declared} or at {fallback}; "
            "set USHABTI_PAPYRUS_PATH to a writable path"
        )
    _log.warning("papyrus_path_not_writable", declared=str(declared), using=str(fallback))
    return fallback


def _provider(spell: Spell, choice: ModelChoice | None, settings: Settings) -> ModelProvider | None:
    spec = spell.spec.model
    if choice is not None:
        spec = spec.model_copy(update={"provider": ModelProviderName(choice.value)})
    try:
        return build_provider(spec, fake_script=settings.fake_script)
    except (ModelError, MissingExtraError) as error:
        # Without a model the ushabti still observes, records, and can be halted.
        _log.warning("no_model", provider=spec.provider.value, reason=str(error))
        return None


def _summary(ushabti: Ushabti, report: TickReport, papyrus: Path, model: str) -> str:
    weighing = ushabti.spell.spec.weighing
    context_only = len(ushabti.spell.spec.maat) - report.observed
    lines = [
        f"ushabti {ushabti.name}: vigil tick {report.tick} complete",
        f"  mode: {ushabti.mode.value}  model: {model}  dry_run: {str(weighing.dry_run).lower()}"
        f"  halted: {str(report.halted).lower()}",
        f"  papyrus: {papyrus}",
        f"  maat: {report.observed} observed, {report.unknown} unknown, "
        f"{context_only} context only",
        f"  isfet: {len(report.isfet)}  omens: {len(report.omens)}"
        f"  summons raised: {len(report.raised)}  answered: {len(report.answered)}",
    ]
    lines += [f"  summons {s.id}: {s.state.value} ({s.detail})" for s in report.answered]
    return "\n".join(lines)


@app.command()
def run(
    spell: Annotated[Path, typer.Option("--spell", help="Path to the spell file.")],
    once: Annotated[bool, typer.Option("--once", help="Run one vigil tick and exit.")] = False,
    model: Annotated[
        ModelChoice | None,
        typer.Option("--model", help="Use this model provider instead of the spell's."),
    ] = None,
) -> None:
    """Run an ushabti. It keeps its vigil and serves its HTTP API until stopped."""
    settings = Settings()
    configure_logging(settings.log_level, settings.log_format)
    loaded = _load(spell)
    provider = _provider(loaded, model, settings)
    path = papyrus_path(loaded, settings)
    ushabti = Ushabti(
        loaded,
        papyrus=JsonlPapyrus(path),
        provider=provider,
        name=settings.name,
        mode=settings.mode,
        url=settings.advertise_url,
    )
    chosen = model.value if model is not None else loaded.spec.model.provider.value
    label = chosen if provider is not None else "none"

    if once:
        report = asyncio.run(ushabti.once())
        typer.echo(_summary(ushabti, report, path, label))
        return

    _log.info(
        "ushabti_starting",
        name=ushabti.name,
        spell=loaded.name,
        mode=ushabti.mode.value,
        model=label,
        dry_run=loaded.spec.weighing.dry_run,
        halted=ushabti.halted,
        papyrus=str(path),
    )
    uvicorn.run(
        create_app(ushabti, settings),
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


@app.command("check-spell")
def check_spell(
    path: Annotated[Path, typer.Argument(help="Path to the spell file.")],
) -> None:
    """Validate a spell and print its resolved configuration, with secrets masked."""
    resolved = resolve_spell(_load(path))
    typer.echo(yaml.dump(resolved.config, Dumper=_BlockDumper, sort_keys=False).rstrip())
    for name in resolved.unset:
        typer.echo(f"warning: environment variable {name} is not set", err=True)
    typer.echo(f"{path}: valid spell", err=True)


def _admin(action: str, url: str) -> None:
    token = Settings().admin_token_value()
    if token is None:
        raise _fail(f"cannot {action}: USHABTI_ADMIN_TOKEN is not set")
    try:
        response = httpx.post(
            f"{url.rstrip('/')}/{action}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except httpx.HTTPError as error:
        raise _fail(f"cannot {action}: could not reach {url}: {error}") from None
    if response.status_code != httpx.codes.OK:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise _fail(f"cannot {action}: {url} answered {response.status_code}: {detail}")
    body = response.json()
    state = "halted" if body.get("halted") else "active"
    note = "" if body.get("changed") else " (no change)"
    typer.echo(f"{body.get('name', url)}: {state}{note}")


@app.command()
def halt(
    url: Annotated[
        str, typer.Option("--url", help="Base URL of the ushabti or overseer.")
    ] = DEFAULT_URL,
) -> None:
    """Halt an ushabti: it stops acting at once and keeps observing."""
    _admin("halt", url)


@app.command()
def resume(
    url: Annotated[str, typer.Option("--url", help="Base URL of the ushabti.")] = DEFAULT_URL,
) -> None:
    """Resume a halted ushabti."""
    _admin("resume", url)


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(f"ushabti-agent {__version__}")


if __name__ == "__main__":  # pragma: no cover
    app()
