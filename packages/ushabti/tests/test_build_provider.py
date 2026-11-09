"""build_provider turns the model block of a spell into a provider."""

from pathlib import Path

import pytest

from ushabti import ModelError, ModelSpec, build_provider
from ushabti.models.anthropic import DEFAULT_MAX_TOKENS, DEFAULT_MODEL, AnthropicProvider
from ushabti.models.fake import FakeProvider
from ushabti.models.openai_compatible import OpenAICompatibleProvider

pytest.importorskip("anthropic")
pytest.importorskip("openai")

ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "LOCAL_KEY": "test-local-key",
    "LOCAL_URL": "http://model.example.invalid:11434/v1",
    "USHABTI_MODEL": "test-model",
}


def spec(**values: object) -> ModelSpec:
    return ModelSpec.model_validate(values)


def test_fake() -> None:
    provider = build_provider(spec(provider="fake"))
    assert isinstance(provider, FakeProvider)
    assert provider.remaining == 0


def test_fake_with_a_script(tmp_path: Path) -> None:
    script = tmp_path / "script.yaml"
    script.write_text("- content: one\n- content: two\n")
    provider = build_provider(spec(provider="fake"), fake_script=script)
    assert isinstance(provider, FakeProvider)
    assert provider.remaining == 2


def test_anthropic_takes_its_model_name_from_the_spell() -> None:
    provider = build_provider(
        spec(provider="anthropic", name="${USHABTI_MODEL}", api_key="${ANTHROPIC_API_KEY}"),
        env=ENV,
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "test-model"
    assert provider.max_tokens == DEFAULT_MAX_TOKENS
    assert provider._client.api_key == "test-anthropic-key"


@pytest.mark.parametrize("name", [None, "${USHABTI_MODEL}"])
def test_anthropic_falls_back_to_its_default_model(name: str | None) -> None:
    # The name is omitted, or it references a variable that is not set.
    env = {"ANTHROPIC_API_KEY": "test-anthropic-key"}
    provider = build_provider(
        spec(provider="anthropic", name=name, api_key="${ANTHROPIC_API_KEY}"), env=env
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == DEFAULT_MODEL


def test_anthropic_honours_max_tokens_and_base_url() -> None:
    provider = build_provider(
        spec(
            provider="anthropic",
            name="test-model",
            api_key="${ANTHROPIC_API_KEY}",
            base_url="${LOCAL_URL}",
            max_tokens=2048,
        ),
        env=ENV,
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.max_tokens == 2048
    assert str(provider._client.base_url).startswith("http://model.example.invalid:11434/v1")


def test_openai_compatible() -> None:
    provider = build_provider(
        spec(
            provider="openai_compatible",
            name="${USHABTI_MODEL}",
            api_key="${LOCAL_KEY}",
            base_url="${LOCAL_URL}",
            max_tokens=512,
        ),
        env=ENV,
    )
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "test-model"
    assert provider.max_tokens == 512
    assert provider._client.api_key == "test-local-key"


def test_openai_compatible_has_no_default_model() -> None:
    with pytest.raises(ModelError, match="needs a model name"):
        build_provider(spec(provider="openai_compatible", name="${USHABTI_MODEL}"), env={})
