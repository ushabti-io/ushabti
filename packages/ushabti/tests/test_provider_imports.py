"""The provider modules import cleanly when their extras are not installed."""

import subprocess
import sys

import pytest

from ushabti import MissingExtraError
from ushabti.models.anthropic import AnthropicProvider
from ushabti.models.openai_compatible import OpenAICompatibleProvider

# Runs in a fresh interpreter where every optional SDK refuses to import. That
# is the same situation as `pip install ushabti` with no extras.
WITHOUT_EXTRAS = """
import sys

BLOCKED = {"anthropic", "openai", "kubernetes", "proxmoxer", "httpx"}


class Blocker:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"{name} is blocked for this test")
        return None


sys.meta_path.insert(0, Blocker())

import ushabti
import ushabti.implements.kubernetes
import ushabti.implements.prometheus
import ushabti.implements.proxmox
import ushabti.models.anthropic
import ushabti.models.fake
import ushabti.models.openai_compatible
from ushabti.errors import MissingExtraError

assert not BLOCKED & set(sys.modules), "an optional SDK was imported"

for provider in (
    ushabti.models.anthropic.AnthropicProvider,
    ushabti.models.openai_compatible.OpenAICompatibleProvider,
):
    try:
        provider(model="test-model")
    except MissingExtraError as error:
        print("missing", error.extra)
    else:
        raise SystemExit(f"{provider.__name__} was built without its SDK")

assert len(ushabti.default_registry()) == 11
print("ok")
"""


def test_the_sdk_imports_and_works_with_no_extras_installed() -> None:
    result = subprocess.run(  # noqa: S603  (our own interpreter, our own script)
        [sys.executable, "-c", WITHOUT_EXTRAS],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["missing", "anthropic", "missing", "openai", "ok"]


def test_building_the_anthropic_provider_without_its_extra_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(MissingExtraError) as caught:
        AnthropicProvider(model="test-model")
    assert caught.value.extra == "anthropic"
    assert 'pip install "ushabti[anthropic]"' in str(caught.value)
    assert isinstance(caught.value, ImportError)


def test_building_the_openai_provider_without_its_extra_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "openai", None)
    with pytest.raises(MissingExtraError) as caught:
        OpenAICompatibleProvider(model="test-model")
    assert caught.value.extra == "openai"
    assert 'pip install "ushabti[openai]"' in str(caught.value)
