"""Spell loading and validation."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from sdk_testkit import spell_data
from ushabti import ImplementRegistry, SpellError, UnresolvedEnvError, load_spell, parse_spell
from ushabti.implements.noop import NoopRead
from ushabti.spell import (
    expand_env,
    find_inline_secrets,
    is_env_reference,
    is_secret_key,
    resolve_optional,
    resolve_secret,
    resolve_spell,
)

SECRET = "sk-live-do-not-leak-0123456789"

EXAMPLE = """
apiVersion: ushabti/v1alpha1
kind: Spell
metadata:
  name: k3s-watcher
spec:
  role: Keep the homelab K3s cluster healthy.
  instructions: |
    Watch deployments in the listed namespaces.
  model:
    provider: anthropic
    name: ${USHABTI_MODEL}
    api_key: ${ANTHROPIC_API_KEY}
  fields:
    - name: homelab-k3s
      kind: kubernetes
      namespaces: [default, monitoring]
  implements:
    - kubernetes.list_pods
    - kubernetes.get_deployment
    - kubernetes.restart_deployment
  maat:
    - id: deployments-ready
      description: Every deployment has ready replicas equal to desired replicas.
  weighing:
    dry_run: true
    require_approval_for: [write]
    max_actions_per_hour: 5
  vigil:
    interval: 60s
  papyrus:
    path: /var/lib/ushabti/papyrus.jsonl
"""


def parse(**spec_overrides: Any) -> Any:
    return parse_spell(yaml.safe_dump(spell_data(**spec_overrides)))


def problems(text: str) -> tuple[str, ...]:
    with pytest.raises(SpellError) as caught:
        parse_spell(text)
    return caught.value.problems


def spec_problems(**spec_overrides: Any) -> tuple[str, ...]:
    return problems(yaml.safe_dump(spell_data(**spec_overrides)))


def test_the_example_spell_loads() -> None:
    spell = parse_spell(EXAMPLE)
    assert spell.name == "k3s-watcher"
    assert spell.api_version == "ushabti/v1alpha1"
    assert spell.spec.model.name == "${USHABTI_MODEL}"
    assert spell.spec.fields[0].namespaces == ("default", "monitoring")
    assert spell.spec.vigil.interval == timedelta(seconds=60)
    assert spell.spec.papyrus.path == Path("/var/lib/ushabti/papyrus.jsonl")


def test_dry_run_is_the_default() -> None:
    assert parse().spec.weighing.dry_run is True


def test_load_spell_reads_a_file(tmp_path: Path) -> None:
    path = tmp_path / "spell.yaml"
    path.write_text(EXAMPLE)
    assert load_spell(path).name == "k3s-watcher"


def test_load_spell_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SpellError, match="cannot read the file"):
        load_spell(tmp_path / "absent.yaml")


# Unknown keys


@pytest.mark.parametrize(
    ("text", "where"),
    [
        (EXAMPLE + "extra: 1\n", "extra"),
        (EXAMPLE.replace("  role:", "  rolee:"), "spec.rolee"),
        (EXAMPLE.replace("    dry_run: true", "    dryrun: true"), "spec.weighing.dryrun"),
        (
            EXAMPLE.replace("      kind: kubernetes", "      kind: kubernetes\n      zone: a"),
            "zone",
        ),
        (EXAMPLE.replace("  name: k3s-watcher", "  name: k3s-watcher\n  owner: me"), "owner"),
    ],
)
def test_unknown_keys_are_rejected(text: str, where: str) -> None:
    found = problems(text)
    assert any(where in problem and "unknown key" in problem for problem in found), found


def test_wrong_api_version_and_kind_are_rejected() -> None:
    assert problems(EXAMPLE.replace("ushabti/v1alpha1", "ushabti/v9"))
    assert problems(EXAMPLE.replace("kind: Spell", "kind: Charm"))


# Implements


def test_unregistered_implements_are_rejected() -> None:
    found = spec_problems(implements=["noop.read", "shell.exec"])
    assert found == ("spec.implements: shell.exec is not a registered implement",)


def test_a_custom_registry_decides_what_is_registered() -> None:
    text = yaml.safe_dump(spell_data(implements=["noop.read"]))
    assert parse_spell(text, registry=ImplementRegistry([NoopRead()])).name == "test-spell"
    with pytest.raises(SpellError, match=r"noop\.read is not a registered implement"):
        parse_spell(text, registry=ImplementRegistry())


def test_duplicates_are_rejected() -> None:
    assert "duplicate implement: noop.read" in spec_problems(implements=["noop.read"] * 2)[0]
    field = {"name": "twin", "kind": "service"}
    assert "duplicate field name: twin" in spec_problems(fields=[field, field])[0]
    assertion = {"id": "same", "description": "x"}
    assert "duplicate maat id: same" in spec_problems(maat=[assertion, assertion])[0]


# Ma'at


def test_maat_must_refer_to_declared_fields_and_listed_read_implements() -> None:
    observe = {"implement": "noop.read", "arguments": {"healthy": True}}
    expect = {"path": "healthy", "op": "eq", "value": True}
    good = {"id": "ok", "description": "x", "observe": observe, "expect": expect}
    assert parse(maat=[good]).spec.maat[0].observe is not None

    assert (
        "field elsewhere is not declared" in spec_problems(maat=[{**good, "field": "elsewhere"}])[0]
    )

    unlisted = {**good, "observe": {"implement": "prometheus.query"}}
    assert "is not listed in implements" in spec_problems(maat=[unlisted])[0]

    writes = {**good, "observe": {"implement": "noop.write"}}
    assert "is a write implement" in spec_problems(maat=[writes])[0]

    assert (
        "observe needs an expect condition"
        in spec_problems(maat=[{"id": "half", "description": "x", "observe": observe}])[0]
    )
    assert (
        "expect and omen need an observe block"
        in spec_problems(maat=[{"id": "half", "description": "x", "expect": expect}])[0]
    )


# Weighing policy


def test_require_approval_for_must_name_a_kind_or_a_listed_implement() -> None:
    assert parse(weighing={"require_approval_for": ["write", "noop.write"]})
    found = spec_problems(weighing={"require_approval_for": ["wriet"]})
    assert "wriet is neither read, write, nor a listed implement" in found[0]


def test_max_actions_per_hour_cannot_be_negative() -> None:
    assert spec_problems(weighing={"max_actions_per_hour": -1})


# Model


def test_openai_compatible_needs_a_model_name() -> None:
    found = spec_problems(model={"provider": "openai_compatible"})
    assert "name is required when provider is openai_compatible" in found[0]
    assert parse(model={"provider": "openai_compatible", "name": "${USHABTI_MODEL}"})


def test_anthropic_may_omit_the_model_name() -> None:
    assert parse(model={"provider": "anthropic"}).spec.model.name is None


def test_max_tokens_is_not_mistaken_for_a_secret() -> None:
    assert parse(model={"provider": "fake", "max_tokens": 1024}).spec.model.max_tokens == 1024


# Vigil


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("500ms", 0.5), ("60s", 60), ("5m", 300), ("1h", 3600), ("1d", 86400), (30, 30), (1.5, 1.5)],
)
def test_vigil_interval_accepts_durations(text: object, seconds: float) -> None:
    assert parse(vigil={"interval": text}).spec.vigil.interval == timedelta(seconds=seconds)


@pytest.mark.parametrize("text", ["soon", "60", "5 minutes", "0s", True, "-5s"])
def test_vigil_interval_rejects_nonsense(text: object) -> None:
    assert spec_problems(vigil={"interval": text})


def test_durations_serialise_back_to_short_strings() -> None:
    for text in ("500ms", "90s", "5m", "2h", "1d"):
        spell = parse(vigil={"interval": text})
        assert spell.model_dump(mode="json")["spec"]["vigil"]["interval"] == text


# Fields


def test_field_keys_must_match_the_kind() -> None:
    assert parse(fields=[{"name": "pve", "kind": "proxmox", "nodes": ["pve1"]}])
    found = spec_problems(fields=[{"name": "pve", "kind": "proxmox", "namespaces": ["default"]}])
    assert "namespaces is only valid when kind is kubernetes" in found[0]
    found = spec_problems(fields=[{"name": "k3s", "kind": "kubernetes", "nodes": ["n1"]}])
    assert "nodes is only valid when kind is proxmox" in found[0]


def test_names_must_be_dns_labels() -> None:
    assert spec_problems(fields=[{"name": "Not Valid", "kind": "service"}])


def test_field_named_finds_a_field_or_the_only_one() -> None:
    one = parse()
    assert one.field_named(None) is one.spec.fields[0]
    assert one.field_named("test-field") is one.spec.fields[0]
    assert one.field_named("absent") is None
    two = parse(fields=[{"name": "a", "kind": "service"}, {"name": "b", "kind": "service"}])
    assert two.field_named(None) is None


# Inline secrets


@pytest.mark.parametrize(
    "key",
    ["api_key", "apiKey", "API_KEY", "token", "admin_token", "password", "secret", "key", "apikey"],
)
def test_secret_looking_keys_are_recognised(key: str) -> None:
    assert is_secret_key(key)


@pytest.mark.parametrize("key", ["max_tokens", "monkey", "keyboard", "tokenizer", "name", "path"])
def test_ordinary_keys_are_not_secrets(key: str) -> None:
    assert not is_secret_key(key)


def test_an_inline_api_key_is_rejected_and_never_echoed() -> None:
    text = EXAMPLE.replace("${ANTHROPIC_API_KEY}", SECRET)
    with pytest.raises(SpellError) as caught:
        parse_spell(text)
    assert caught.value.problems == (
        "spec.model.api_key: a secret must be an environment reference such as ${VAR}, "
        "never a literal value",
    )
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_a_partial_reference_is_still_a_literal() -> None:
    found = problems(EXAMPLE.replace("${ANTHROPIC_API_KEY}", "Bearer ${ANTHROPIC_API_KEY}"))
    assert found[0].startswith("spec.model.api_key")


def test_inline_secrets_are_found_before_unknown_keys_hide_them() -> None:
    text = EXAMPLE.replace("  role:", f"  password: {SECRET}\n  role:")
    found = problems(text)
    assert found == (
        "spec.password: a secret must be an environment reference such as ${VAR}, "
        "never a literal value",
    )


def test_inline_secrets_are_found_anywhere_in_the_tree() -> None:
    data = {
        "a": {"token": "literal"},
        "b": [{"apiKey": "literal"}, {"apiKey": "${FINE}"}],
        "c": {"secret": ["literal", "${FINE}"]},
        "d": {"secret": {"name": "a-reference-to-somewhere"}},
        "e": {"password": None, "key": "${FINE}"},
        "f": {"private_key": 12345},
    }
    assert find_inline_secrets(data) == ["a.token", "b[0].apiKey", "c.secret", "f.private_key"]


def test_environment_references_are_accepted_for_secrets() -> None:
    assert parse_spell(EXAMPLE).spec.model.api_key == "${ANTHROPIC_API_KEY}"


# Environment references


def test_is_env_reference() -> None:
    assert is_env_reference("${API_KEY}")
    assert not is_env_reference("prefix ${API_KEY}")
    assert not is_env_reference("$API_KEY")
    assert not is_env_reference("${}")
    assert not is_env_reference(42)


def test_expand_env() -> None:
    env = {"HOST": "example.invalid", "PORT": "9090", "EMPTY": ""}
    assert expand_env("http://${HOST}:${PORT}/api", env) == "http://example.invalid:9090/api"
    assert expand_env("no references", env) == "no references"
    with pytest.raises(UnresolvedEnvError) as caught:
        expand_env("${MISSING} ${EMPTY} ${MISSING}", env)
    assert caught.value.variables == ("MISSING", "EMPTY")


def test_expand_env_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USHABTI_TEST_VALUE", "from-the-environment")
    assert expand_env("${USHABTI_TEST_VALUE}") == "from-the-environment"


def test_resolve_optional_and_secret() -> None:
    env = {"SET": "value"}
    assert resolve_optional(None, env) is None
    assert resolve_optional("${SET}", env) == "value"
    assert resolve_optional("${UNSET}", env) is None
    assert resolve_secret(None, env) is None
    assert resolve_secret("${UNSET}", env) is None
    secret = resolve_secret("${SET}", env)
    assert secret is not None
    assert secret.get_secret_value() == "value"
    assert "value" not in repr(secret)


def test_resolve_spell_fills_references_and_masks_secrets() -> None:
    spell = parse_spell(EXAMPLE)
    resolved = resolve_spell(spell, {"USHABTI_MODEL": "test-model", "ANTHROPIC_API_KEY": SECRET})
    model = resolved.config["spec"]["model"]  # type: ignore[index, call-overload]
    assert model["name"] == "test-model"  # type: ignore[index, call-overload]
    assert model["api_key"] == "********"  # type: ignore[index, call-overload]
    assert resolved.unset == ()
    assert SECRET not in str(resolved.config)
    assert resolved.config["apiVersion"] == "ushabti/v1alpha1"


def test_resolve_spell_reports_unset_variables() -> None:
    resolved = resolve_spell(parse_spell(EXAMPLE), {})
    model = resolved.config["spec"]["model"]  # type: ignore[index, call-overload]
    assert model["name"] == "<unset ${USHABTI_MODEL}>"  # type: ignore[index, call-overload]
    assert model["api_key"] == "<unset ${ANTHROPIC_API_KEY}>"  # type: ignore[index, call-overload]
    assert resolved.unset == ("USHABTI_MODEL", "ANTHROPIC_API_KEY")


# Malformed documents


def test_invalid_yaml_is_reported_without_quoting_the_file() -> None:
    with pytest.raises(SpellError) as caught:
        parse_spell(f"api_key: [{SECRET}\n  broken: : :")
    assert "not valid YAML" in caught.value.problems[0]
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("text", ["", "- a\n- b\n", "just a string"])
def test_the_document_must_be_a_mapping(text: str) -> None:
    assert problems(text) == ("the document must be a mapping",)


def test_missing_required_keys_are_reported_by_path() -> None:
    found = problems("apiVersion: ushabti/v1alpha1\nkind: Spell\nmetadata: {name: x}\nspec: {}\n")
    assert "spec.role: Field required" in found
    assert "spec.model: Field required" in found


def test_spell_error_message_lists_every_problem() -> None:
    error = SpellError(["first", "second"], source="my.yaml")
    assert str(error) == "my.yaml is not a valid spell:\n  - first\n  - second"
