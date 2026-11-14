"""The commit checks catch private detail: the gitleaks rules and the terms script.

The samples here have the shape of private detail and belong to nobody.
`.gitleaks.toml` exempts this one file from the rules it tests.
"""

import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[3]
GITLEAKS = REPO / ".gitleaks.toml"
SCRIPT = REPO / "scripts" / "check_sensitive_terms.py"

pytestmark = pytest.mark.skipif(
    not (GITLEAKS.exists() and SCRIPT.exists()), reason="the repository checks are not present"
)


def rule(rule_id: str) -> dict[str, Any]:
    config = tomllib.loads(GITLEAKS.read_text(encoding="utf-8"))
    found: dict[str, Any] = next(r for r in config["rules"] if r["id"] == rule_id)
    return found


def flagged(rule_id: str, text: str) -> bool:
    """Whether the rule reports the text, after its own allowlists."""
    entry = rule(rule_id)
    match = re.search(entry["regex"], text)
    if match is None:
        return False
    allowed = [a for group in entry.get("allowlists", []) for a in group.get("regexes", [])]
    return not any(re.search(pattern, match.group(0)) for pattern in allowed)


def path_flagged(path: str) -> bool:
    entry = rule("sensitive-file")
    if re.search(entry["path"], path) is None:
        return False
    allowed = [a for group in entry.get("allowlists", []) for a in group.get("paths", [])]
    return not any(re.search(pattern, path) for pattern in allowed)


@pytest.mark.parametrize(
    ("rule_id", "text"),
    [
        ("tailnet-hostname", "https://grafana.some-tailnet.ts.net/d/abc"),
        ("tailnet-hostname", "host: NODE-1.Some-Tailnet.TS.NET"),
        ("private-ip-address", "server: https://10.0.0.5:6443"),
        ("private-ip-address", "192.168.1.20"),
        ("private-ip-address", "172.16.0.1"),
        ("private-ip-address", "172.31.255.254"),
        ("private-ip-address", "100.64.0.1"),
        ("private-ip-address", "100.127.3.4"),
        ("local-network-hostname", "http://nas.lan:5000"),
        ("local-network-hostname", "proxmox.home.arpa"),
        ("email-address", "contact: someone@mail.example.dev"),
    ],
)
def test_a_rule_flags_private_detail(rule_id: str, text: str) -> None:
    assert flagged(rule_id, text)


@pytest.mark.parametrize(
    ("rule_id", "text"),
    [
        ("tailnet-hostname", "https://prometheus.example.invalid"),
        ("private-ip-address", "192.0.2.10"),
        ("private-ip-address", "198.51.100.7"),
        ("private-ip-address", "172.15.0.1"),
        ("private-ip-address", "172.32.0.1"),
        ("private-ip-address", "100.63.0.1"),
        ("private-ip-address", "100.128.0.1"),
        ("private-ip-address", "110.1.2.3"),
        ("private-ip-address", "version 10.2.1"),
        ("local-network-hostname", "ushabti.default.svc.cluster.local"),
        ("local-network-hostname", "the plan is simple"),
        ("email-address", "operator@example.invalid"),
        ("email-address", "someone@example.com"),
        ("email-address", "1234+someone@users.noreply.github.com"),
        ("email-address", "git@github.com:ushabti-io/ushabti.git"),
        ("email-address", "noreply@anthropic.com"),
        ("email-address", "ghcr.io/astral-sh/uv@sha256:0123abcd"),
    ],
)
def test_a_rule_passes_public_detail(rule_id: str, text: str) -> None:
    assert not flagged(rule_id, text)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "deploy/compose/.env.production",
        "kubeconfig",
        "deploy/k3s-kubeconfig.yaml",
        "certs/server.pem",
        "tls.key",
        "infra/terraform.tfstate",
        "infra/terraform.tfstate.backup",
        "infra/prod.tfvars",
        "home/.ssh/id_ed25519",
    ],
)
def test_the_file_rule_flags_credential_files(path: str) -> None:
    assert path_flagged(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", "docs/environment.md", "packages/ushabti/src/ushabti/keys.py", "id_rsa.pub"],
)
def test_the_file_rule_passes_ordinary_files(path: str) -> None:
    assert not path_flagged(path)


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_sensitive_terms", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return load_script()


TERMS = """\
# names of hosts
Bakery-Tailnet

re: \\bnode-\\d+\\b
"""


def write_terms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str = TERMS) -> None:
    terms = tmp_path / "terms.txt"
    terms.write_text(text, encoding="utf-8")
    monkeypatch.setenv("SENSITIVE_TERMS_FILE", str(terms))


def test_terms_skip_comments_and_blank_lines(script: ModuleType) -> None:
    terms = script.load_terms(TERMS)
    assert [term.line for term in terms] == [2, 4]


def test_a_plain_term_matches_anywhere_and_ignores_case(script: ModuleType) -> None:
    terms = script.load_terms("a.b")
    assert script.scan("notes.md", "first\nsee XA.Bx here", terms) == [("notes.md", 2, 1)]
    assert script.scan("notes.md", "see aXb here", terms) == []


def test_a_term_matches_a_file_name(script: ModuleType) -> None:
    terms = script.load_terms("bakery")
    assert script.scan("docs/Bakery.md", "nothing", terms) == [("docs/Bakery.md", 0, 1)]


@pytest.mark.parametrize(
    "path", [".git/COMMIT_EDITMSG", "/home/bakery/repo/.git/worktrees/fix/COMMIT_EDITMSG"]
)
def test_the_location_of_a_commit_message_is_not_checked(script: ModuleType, path: str) -> None:
    terms = script.load_terms("bakery\ngit")
    assert script.scan(path, "a clean message", terms) == []
    assert script.scan(path, "from the bakery", terms) == [(path, 1, 1)]


def test_a_bad_expression_names_its_line(script: ModuleType) -> None:
    with pytest.raises(ValueError, match="line 2"):
        script.load_terms("fine\nre: (")


def test_a_finding_fails_and_never_prints_the_term(
    script: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_terms(tmp_path, monkeypatch)
    staged = tmp_path / "spell.yaml"
    staged.write_text("name: tend\nurl: https://x.bakery-tailnet.example.invalid\nhost: node-12\n")

    assert script.main([str(staged)]) == 1

    output = capsys.readouterr().err
    assert f"{staged}:2: matches line 2" in output
    assert f"{staged}:3: matches line 4" in output
    assert "bakery" not in output.lower()
    assert "node-12" not in output


def test_a_clean_file_passes(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_terms(tmp_path, monkeypatch)
    staged = tmp_path / "spell.yaml"
    staged.write_text("url: https://prometheus.example.invalid\n")
    assert script.main([str(staged), str(tmp_path / "deleted.yaml")]) == 0


def test_no_terms_file_passes(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SENSITIVE_TERMS_FILE", raising=False)
    monkeypatch.setattr(script, "DEFAULT_TERMS_FILE", tmp_path / "absent.txt")
    assert script.main([str(tmp_path / "anything")]) == 0


def test_a_named_terms_file_that_is_missing_fails(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SENSITIVE_TERMS_FILE", str(tmp_path / "absent.txt"))
    assert script.main([]) == 1


def test_an_unusable_terms_file_fails(
    script: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_terms(tmp_path, monkeypatch, "re: (")
    assert script.main([]) == 1
