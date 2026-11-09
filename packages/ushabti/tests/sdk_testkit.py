"""Helpers shared by the SDK tests."""

from typing import Any

from ushabti import Spell, Verdict, WeighingResult


def spell_data(**spec_overrides: Any) -> dict[str, Any]:
    """A minimal valid spell as plain data. Keyword arguments replace spec keys."""
    spec: dict[str, Any] = {
        "role": "Tend the test field.",
        "model": {"provider": "fake"},
        "fields": [{"name": "test-field", "kind": "service"}],
        "implements": ["noop.read", "noop.write"],
    }
    spec.update(spec_overrides)
    return {
        "apiVersion": "ushabti/v1alpha1",
        "kind": "Spell",
        "metadata": {"name": "test-spell"},
        "spec": spec,
    }


def make_spell(**spec_overrides: Any) -> Spell:
    return Spell.model_validate(spell_data(**spec_overrides))


def weighed(implement: str, verdict: Verdict = Verdict.PASS) -> WeighingResult:
    return WeighingResult(implement=implement, verdict=verdict, rule="test", reason="for a test")
