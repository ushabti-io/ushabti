"""The spell: the declarative definition of one ushabti."""

import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, Literal, Self

import pydantic
import yaml
from pydantic import AfterValidator, ConfigDict, JsonValue, PositiveInt, SecretStr, model_validator

from ushabti._base import Duration, Name, StrictModel
from ushabti.errors import SpellError, UnresolvedEnvError
from ushabti.field import Field
from ushabti.implements import ImplementKind, ImplementRegistry, default_registry
from ushabti.maat import MaatAssertion
from ushabti.weighing import WeighingPolicy

API_VERSION: Final = "ushabti/v1alpha1"

ENV_REFERENCE: Final = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WHOLE_REFERENCE: Final = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

# Splits snake_case, kebab-case, and camelCase keys into words.
_WORDS: Final = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
_SECRET_WORDS: Final = frozenset({"token", "password", "passwd", "secret", "key", "apikey"})
_MASK: Final = "********"


def is_secret_key(key: str) -> bool:
    """Whether a key is named like a secret: token, password, secret, or key.

    The match is on whole words, so ``api_key`` and ``adminToken`` count and
    ``max_tokens`` and ``monkey`` do not.
    """
    return any(word.lower() in _SECRET_WORDS for word in _WORDS.findall(key))


def is_env_reference(value: object) -> bool:
    """Whether a value is exactly one environment reference, such as ``${API_KEY}``."""
    return isinstance(value, str) and _WHOLE_REFERENCE.match(value) is not None


def expand_env(value: str, env: Mapping[str, str] | None = None) -> str:
    """Replace every ``${VAR}`` in a string. An unset or empty variable is an error."""
    source = os.environ if env is None else env
    missing = [name for name in ENV_REFERENCE.findall(value) if not source.get(name)]
    if missing:
        raise UnresolvedEnvError(dict.fromkeys(missing))
    return ENV_REFERENCE.sub(lambda match: source[match.group(1)], value)


def resolve_optional(value: str | None, env: Mapping[str, str] | None = None) -> str | None:
    """Expand a value, or return None when it is absent or references an unset variable."""
    if value is None:
        return None
    try:
        return expand_env(value, env)
    except UnresolvedEnvError:
        return None


def resolve_secret(reference: str | None, env: Mapping[str, str] | None = None) -> SecretStr | None:
    """Read the secret a reference points at, or None when it is absent or unset."""
    resolved = resolve_optional(reference, env)
    return None if resolved is None else SecretStr(resolved)


def find_inline_secrets(data: object, path: str = "") -> list[str]:
    """Paths of keys named like secrets that hold a literal value.

    A secret may only be written as an environment reference. The values
    themselves are never returned, so they cannot leak into an error message.
    """
    found: list[str] = []
    if isinstance(data, Mapping):
        for key, value in data.items():
            where = f"{path}.{key}" if path else str(key)
            if isinstance(value, Mapping):
                found.extend(find_inline_secrets(value, where))
            elif isinstance(key, str) and is_secret_key(key):
                items = value if isinstance(value, list) else [value]
                if any(_is_literal(item) for item in items):
                    found.append(where)
                found.extend(find_inline_secrets([i for i in items if not _is_scalar(i)], where))
            else:
                found.extend(find_inline_secrets(value, where))
    elif isinstance(data, list):
        for index, item in enumerate(data):
            found.extend(find_inline_secrets(item, f"{path}[{index}]"))
    return found


def _is_scalar(value: object) -> bool:
    return not isinstance(value, Mapping | list)


def _is_literal(value: object) -> bool:
    return _is_scalar(value) and value is not None and not is_env_reference(value)


def _check_secret_reference(value: str) -> str:
    if not is_env_reference(value):
        raise ValueError(
            "must be an environment reference such as ${ANTHROPIC_API_KEY}, never a literal value"
        )
    return value


SecretReference = Annotated[str, AfterValidator(_check_secret_reference)]
"""A string that must be an environment reference and never a literal secret."""


class SpellMetadata(StrictModel):
    name: Name
    """Name of the spell. The ushabti running it takes this name unless
    `USHABTI_NAME` says otherwise."""


class ModelProviderName(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"
    FAKE = "fake"


class ModelSpec(StrictModel):
    """Which model answers a summons, and how to reach it."""

    provider: ModelProviderName
    """Model provider: `anthropic`, `openai_compatible`, or `fake`."""

    name: str | None = None
    """Model name, usually an environment reference such as `${USHABTI_MODEL}`.
    Required for `openai_compatible`. The `anthropic` provider falls back to its
    default model when this is omitted or the variable is unset."""

    api_key: SecretReference | None = None
    """API key as an environment reference such as `${ANTHROPIC_API_KEY}`. A
    literal value is rejected."""

    base_url: str | None = None
    """Base URL of the endpoint. Use it to point `openai_compatible` at a local
    server such as Ollama or vLLM."""

    max_tokens: PositiveInt | None = None
    """Upper bound on tokens per model reply. Each provider has its own default."""

    max_steps: PositiveInt = 8
    """How many times the model may be asked for implement calls while answering
    one summons."""

    @model_validator(mode="after")
    def _name_where_needed(self) -> Self:
        if self.provider is ModelProviderName.OPENAI_COMPATIBLE and self.name is None:
            raise ValueError("name is required when provider is openai_compatible")
        return self


class VigilSpec(StrictModel):
    """How often the ushabti observes its fields."""

    interval: Duration = timedelta(seconds=60)
    """Time between vigil ticks, such as `60s` or `5m`."""

    @model_validator(mode="after")
    def _interval_is_positive(self) -> Self:
        if self.interval <= timedelta(0):
            raise ValueError("interval must be greater than zero")
        return self


class PapyrusSpec(StrictModel):
    """Where the ushabti writes its audit log."""

    path: Path = Path("papyrus.jsonl")
    """Path of the JSON Lines file. `USHABTI_PAPYRUS_PATH` overrides it."""


class SpellSpec(StrictModel):
    role: str
    """One line on what this ushabti is for. The model reads this."""

    instructions: str = ""
    """Standing instructions for the model."""

    model: ModelSpec
    """Which model answers a summons (a unit of work)."""

    fields: tuple[Field, ...] = ()
    """The fields (targets) this ushabti tends."""

    implements: tuple[str, ...] = ()
    """Names of the implements (tools) this ushabti may use. Anything not listed
    here is rejected by the weighing."""

    maat: tuple[MaatAssertion, ...] = ()
    """Ma'at: assertions about what healthy looks like."""

    weighing: WeighingPolicy = WeighingPolicy()
    """Policy for the weighing, the safety gate every write implement must pass."""

    vigil: VigilSpec = VigilSpec()
    """The vigil, the monitoring loop."""

    papyrus: PapyrusSpec = PapyrusSpec()
    """Papyrus, the append-only audit log."""

    @model_validator(mode="after")
    def _parts_agree(self) -> Self:
        problems: list[str] = []
        field_names = [field.name for field in self.fields]
        problems += [f"duplicate field name: {n}" for n in _duplicates(field_names)]
        problems += [f"duplicate implement: {n}" for n in _duplicates(self.implements)]
        problems += [f"duplicate maat id: {n}" for n in _duplicates(a.id for a in self.maat)]
        for assertion in self.maat:
            if assertion.field is not None and assertion.field not in field_names:
                problems.append(f"maat {assertion.id}: field {assertion.field} is not declared")
            if assertion.observe is not None and assertion.observe.implement not in self.implements:
                problems.append(
                    f"maat {assertion.id}: observe.implement {assertion.observe.implement} "
                    "is not listed in implements"
                )
        kinds = {kind.value for kind in ImplementKind}
        for entry in self.weighing.require_approval_for:
            if entry not in kinds and entry not in self.implements:
                problems.append(
                    f"weighing.require_approval_for: {entry} is neither read, write, "
                    "nor a listed implement"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


class Spell(StrictModel):
    """The declarative definition of one ushabti."""

    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    api_version: Literal["ushabti/v1alpha1"] = pydantic.Field(alias="apiVersion")
    """Schema version. Always `ushabti/v1alpha1`."""

    kind: Literal["Spell"]
    """Always `Spell`."""

    metadata: SpellMetadata
    """Identity of the spell."""

    spec: SpellSpec
    """What the ushabti does and how it is kept safe."""

    @property
    def name(self) -> str:
        return self.metadata.name

    def field_named(self, name: str | None) -> Field | None:
        """The declared field with this name, or the only field when name is None."""
        if name is None:
            return self.spec.fields[0] if len(self.spec.fields) == 1 else None
        return next((field for field in self.spec.fields if field.name == name), None)


@dataclass(frozen=True, slots=True)
class ResolvedSpell:
    """A spell with its environment references filled in, safe to print."""

    config: dict[str, JsonValue]
    """The spell as plain data. Secrets are masked and never appear in the clear."""

    unset: tuple[str, ...]
    """Names of referenced environment variables that are not set."""


def load_spell(path: Path | str, *, registry: ImplementRegistry | None = None) -> Spell:
    """Read and validate a spell file.

    Rejects unknown keys, implements that are not registered, and secrets
    written into the file. Environment references are kept as references: they
    are resolved when a value is needed, so a spell can be checked on a machine
    that does not hold its secrets.
    """
    location = Path(path)
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as error:
        raise SpellError([f"cannot read the file: {error.strerror}"], source=str(path)) from None
    return parse_spell(text, registry=registry, source=str(path))


def parse_spell(
    text: str, *, registry: ImplementRegistry | None = None, source: str = "spell"
) -> Spell:
    """Validate spell YAML held in a string. See ``load_spell``."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        # The parser's own message can quote the file, which might hold a secret.
        mark = getattr(error, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise SpellError([f"not valid YAML{where}"], source=source) from None
    if not isinstance(data, dict):
        raise SpellError(["the document must be a mapping"], source=source)

    inline = find_inline_secrets(data)
    if inline:
        raise SpellError(
            [
                f"{where}: a secret must be an environment reference such as ${{VAR}}, "
                "never a literal value"
                for where in inline
            ],
            source=source,
        )

    try:
        spell = Spell.model_validate(data)
    except pydantic.ValidationError as error:
        raise SpellError(_validation_problems(error), source=source) from None

    problems = check_implements(spell, default_registry() if registry is None else registry)
    if problems:
        raise SpellError(problems, source=source)
    return spell


def check_implements(spell: Spell, registry: ImplementRegistry) -> list[str]:
    """Problems with how a spell uses implements, given what is registered."""
    problems = [
        f"spec.implements: {name} is not a registered implement"
        for name in spell.spec.implements
        if name not in registry
    ]
    for assertion in spell.spec.maat:
        if assertion.observe is None:
            continue
        if registry.kind_of(assertion.observe.implement) is ImplementKind.WRITE:
            problems.append(
                f"spec.maat {assertion.id}: observe.implement {assertion.observe.implement} "
                "is a write implement; the vigil only observes through read implements"
            )
    return problems


def resolve_spell(spell: Spell, env: Mapping[str, str] | None = None) -> ResolvedSpell:
    """Fill in a spell's environment references for display, masking secrets."""
    source = os.environ if env is None else env
    unset: dict[str, None] = {}

    def fill(value: JsonValue, secret: bool) -> JsonValue:
        if isinstance(value, dict):
            return {key: fill(item, is_secret_key(key)) for key, item in value.items()}
        if isinstance(value, list):
            return [fill(item, secret) for item in value]
        if not isinstance(value, str):
            return value
        names = ENV_REFERENCE.findall(value)
        missing = [name for name in names if not source.get(name)]
        unset.update(dict.fromkeys(missing))
        if missing:
            return f"<unset {' '.join(f'${{{name}}}' for name in missing)}>"
        if secret:
            return _MASK
        return ENV_REFERENCE.sub(lambda match: source[match.group(1)], value)

    dumped: JsonValue = spell.model_dump(mode="json", by_alias=True)
    config = fill(dumped, False)
    if not isinstance(config, dict):  # pragma: no cover  (a spell always dumps to a mapping)
        raise TypeError("a spell must dump to a mapping")
    return ResolvedSpell(config=config, unset=tuple(unset))


def _duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    repeated: dict[str, None] = {}
    for value in values:
        if value in seen:
            repeated[value] = None
        seen.add(value)
    return list(repeated)


def _validation_problems(error: pydantic.ValidationError) -> list[str]:
    problems: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        where = ".".join(str(part) for part in item["loc"]) or "spell"
        message = "unknown key" if item["type"] == "extra_forbidden" else item["msg"]
        problems.append(f"{where}: {message.removeprefix('Value error, ')}")
    return problems
