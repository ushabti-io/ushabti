"""The documentation stays true: links resolve and generated pages have not drifted."""

import re
import tomllib
from pathlib import Path

import pytest

from ushabti import ImplementRegistry, Verdict, WeighingPolicy, WeighingRule
from ushabti.implements import Implement
from ushabti.reference import spell_reference

REPO = Path(__file__).resolve().parents[3]
DOCS = REPO / "docs"

pytestmark = pytest.mark.skipif(
    not (DOCS / "glossary.md").exists(), reason="the repository docs are not present"
)

PAGES = [
    REPO / "README.md",
    REPO / "AGENTS.md",
    REPO / "CONTRIBUTING.md",
    REPO / "CHANGELOG.md",
    REPO / "SECURITY.md",
    REPO / "CODE_OF_CONDUCT.md",
    *sorted(DOCS.glob("*.md")),
    *sorted(REPO.glob("packages/*/README.md")),
]

LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^```.*?^```", re.S | re.M)
MERMAID = re.compile(r"^```mermaid\n(.*?)^```", re.S | re.M)


def prose(path: Path) -> str:
    """The page without its code blocks, where brackets are not links."""
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(line.lstrip("#")) for line in prose(path).splitlines() if line.startswith("#")}


def relative_links() -> list[tuple[Path, str]]:
    found = []
    for page in PAGES:
        for target in LINK.findall(prose(page)):
            if not target.startswith(("http://", "https://", "mailto:")):
                found.append((page, target))
    return found


def test_every_page_exists() -> None:
    names = {page.name for page in DOCS.glob("*.md")}
    assert names == {"glossary.md", "architecture.md", "safety.md", "spells.md"}
    for page in PAGES:
        assert page.exists(), page


@pytest.mark.parametrize(
    ("page", "target"), relative_links(), ids=lambda v: v.name if isinstance(v, Path) else v
)
def test_every_relative_link_resolves(page: Path, target: str) -> None:
    file_part, _, anchor = target.partition("#")
    destination = (page.parent / file_part).resolve() if file_part else page
    assert destination.exists(), f"{page.name} links to {target}, which does not exist"
    if anchor:
        assert destination.suffix == ".md", f"{target} has an anchor into a non-Markdown file"
        assert anchor in anchors(destination), f"{page.name} links to a missing heading: {target}"


def test_the_docs_have_relative_links_to_check() -> None:
    targets = {target for _, target in relative_links()}
    assert "docs/glossary.md" in targets, "the README links to the glossary"
    assert len(targets) > 15


def test_the_spell_reference_is_generated_from_the_model() -> None:
    assert (DOCS / "spells.md").read_text(encoding="utf-8") == spell_reference(), (
        "docs/spells.md is out of date. "
        "Regenerate it with: uv run python -m ushabti.reference > docs/spells.md"
    )


def test_the_spell_reference_covers_every_key_of_the_weighing_policy() -> None:
    reference = spell_reference()
    for key in WeighingPolicy.model_fields:
        assert f"| `{key}` |" in reference
    assert "| `dry_run` | boolean | `true` |" in reference


def test_the_safety_page_names_every_rule_verdict_and_default() -> None:
    safety = (DOCS / "safety.md").read_text(encoding="utf-8")
    for rule in WeighingRule:
        assert f"`{rule.value}`" in safety, rule
    for verdict in Verdict:
        assert f"`{verdict.value}`" in safety, verdict
    policy = WeighingPolicy()
    assert f"`dry_run: {str(policy.dry_run).lower()}`" in safety
    assert f"`require_approval_for: [{', '.join(policy.require_approval_for)}]`" in safety
    assert f"`max_actions_per_hour: {policy.max_actions_per_hour}`" in safety


def test_the_safety_page_lists_the_rules_in_the_order_they_apply() -> None:
    safety = (DOCS / "safety.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (\d) \| .*? \| `(\w+)` \| `(\w+)` \|$", safety, re.M)
    assert [(int(n), rule) for n, _, rule in rows] == list(
        enumerate((rule.value for rule in WeighingRule), start=1)
    )


def test_the_readme_gives_the_other_spellings_and_the_safety_defaults() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "shabti" in readme
    assert "shawabti" in readme
    assert "`dry_run: true` by default" in readme
    assert "docs/glossary.md" in readme
    for mode in ("`solo`", "`independent`", "`overseen`"):
        assert mode in readme


def test_mermaid_labels_break_lines_with_br_and_never_backslash_n() -> None:
    diagrams = MERMAID.findall((DOCS / "architecture.md").read_text(encoding="utf-8"))
    assert len(diagrams) == 2
    for diagram in diagrams:
        assert "\\n" not in diagram
        assert "<br/>" in diagram


def test_the_contributing_example_is_a_real_implement() -> None:
    page = (REPO / "CONTRIBUTING.md").read_text(encoding="utf-8")
    block = re.search(r"^   ```python\n(.*?)^   ```", page, re.S | re.M)
    assert block is not None
    code = "\n".join(line[3:] for line in block.group(1).splitlines())
    namespace: dict[str, object] = {}
    exec(code, namespace)  # noqa: S102  (our own documentation, checked on purpose)
    made = [
        value
        for value in namespace.values()
        if isinstance(value, type) and issubclass(value, Implement) and value is not Implement
    ]
    assert len(made) == 1
    registry = ImplementRegistry([made[0]()])
    assert registry.names() == ("proxmox.node_uptime",)


def test_claude_md_imports_agents_md_so_every_agent_reads_one_file() -> None:
    claude = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.splitlines()[0] == "@AGENTS.md", "CLAUDE.md must import AGENTS.md first"

    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    assert len(agents.splitlines()) < 200, "keep AGENTS.md short: long files are followed less"
    for command in ("uv run ruff check .", "uv run mypy packages", "uv run pytest"):
        assert command in agents, command


def test_only_the_sdk_can_be_uploaded_to_pypi() -> None:
    def classifiers(package: str) -> list[str]:
        text = (REPO / "packages" / package / "pyproject.toml").read_text(encoding="utf-8")
        found: list[str] = tomllib.loads(text)["project"]["classifiers"]
        return found

    private = "Private :: Do Not Upload"
    assert private not in classifiers("ushabti"), "the SDK is the one package on PyPI"
    assert private in classifiers("ushabti-agent"), "the agent ships as a container image"
    assert private in classifiers("ushabti-overseer"), "the overseer ships as a container image"

    release = (REPO / ".github" / "workflows" / "release.yaml").read_text(encoding="utf-8")
    assert "uv build --package ushabti --no-sources" in release
    assert "--all-packages --no-sources" not in release


# These hold only while SCAFFOLD.md is kept in the repository.

SCAFFOLD = REPO / "SCAFFOLD.md"
needs_scaffold = pytest.mark.skipif(not SCAFFOLD.exists(), reason="SCAFFOLD.md has been removed")


@needs_scaffold
def test_the_glossary_is_section_2_of_the_scaffold_verbatim() -> None:
    scaffold = SCAFFOLD.read_text(encoding="utf-8")
    section = re.search(r"^## 2\. Glossary\n\n(.*?)\n---\n\n## 3\.", scaffold, re.S | re.M)
    assert section is not None
    glossary = (DOCS / "glossary.md").read_text(encoding="utf-8")
    assert glossary.startswith("# Glossary\n\n" + section.group(1).rstrip("\n") + "\n")


@needs_scaffold
def test_the_architecture_diagrams_are_the_ones_in_the_scaffold() -> None:
    scaffold = MERMAID.findall(SCAFFOLD.read_text(encoding="utf-8"))
    architecture = MERMAID.findall((DOCS / "architecture.md").read_text(encoding="utf-8"))
    assert architecture == scaffold


@needs_scaffold
def test_the_example_spell_is_the_one_in_the_scaffold() -> None:
    scaffold = SCAFFOLD.read_text(encoding="utf-8")
    block = re.search(r"k3s-watcher\.yaml`\):\n\n```yaml\n(.*?)```", scaffold, re.S)
    assert block is not None
    example = (REPO / "examples" / "spells" / "k3s-watcher.yaml").read_text(encoding="utf-8")
    assert example == block.group(1)
