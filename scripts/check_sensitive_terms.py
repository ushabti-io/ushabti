"""Refuse a commit that holds a term from your private terms file.

The terms file lives outside the repository, because this repository is public
and the terms are the very thing that must not be published. It is read from
the path in SENSITIVE_TERMS_FILE, or from ~/.config/sensitive-terms.txt.

One term per line. A term matches anywhere in a line, ignoring case. A line
that starts with "re:" is a regular expression. Blank lines and lines that
start with "#" are skipped.

With no terms file the check passes, so a contributor who has none is not
blocked. If SENSITIVE_TERMS_FILE is set and the file is missing, it fails.

A finding names the file, the line, and the line of the terms file that
matched. It never prints the term or the text that matched.
"""

import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

ENV_VAR = "SENSITIVE_TERMS_FILE"
DEFAULT_TERMS_FILE = Path("~/.config/sensitive-terms.txt")
REGEX_PREFIX = "re:"


class Term(NamedTuple):
    line: int
    """The line of the terms file this term came from."""
    pattern: re.Pattern[str]


class Finding(NamedTuple):
    path: str
    line: int
    """The line of the checked file, or 0 when the file's own name matched."""
    term_line: int


def load_terms(text: str) -> list[Term]:
    """Parse the text of a terms file."""
    terms = []
    for number, raw in enumerate(text.splitlines(), start=1):
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith(REGEX_PREFIX):
            source = entry.removeprefix(REGEX_PREFIX).strip()
        else:
            source = re.escape(entry)
        try:
            pattern = re.compile(source, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"line {number} is not a valid regular expression: {error}") from error
        terms.append(Term(number, pattern))
    return terms


def in_repository(path: str) -> bool:
    """Whether the path names a file that would be committed.

    The commit message does not: git passes it as a path under .git, and as an
    absolute path in a worktree. Its location says nothing about the commit.
    """
    candidate = Path(path)
    return not candidate.is_absolute() and candidate.parts[:1] != (".git",)


def scan(path: str, text: str, terms: list[Term]) -> list[Finding]:
    """Find every term in the lines of one file, and in its name if it is committed."""
    findings: list[Finding] = []
    if in_repository(path):
        findings.extend(Finding(path, 0, term.line) for term in terms if term.pattern.search(path))
    for number, line in enumerate(text.splitlines(), start=1):
        findings.extend(
            Finding(path, number, term.line) for term in terms if term.pattern.search(line)
        )
    return findings


def terms_file() -> tuple[Path, bool]:
    """The path of the terms file, and whether the environment named it."""
    named = os.environ.get(ENV_VAR)
    if named:
        return Path(named).expanduser(), True
    return DEFAULT_TERMS_FILE.expanduser(), False


def main(paths: list[str]) -> int:
    source, required = terms_file()
    if not source.is_file():
        if required:
            sys.stderr.write(f"{ENV_VAR} is set, but there is no file at that path.\n")
            return 1
        return 0
    try:
        terms = load_terms(source.read_text(encoding="utf-8"))
    except ValueError as error:
        sys.stderr.write(f"The sensitive terms file is not usable: {error}\n")
        return 1

    findings: list[Finding] = []
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            # A deleted file or a directory, such as a submodule. Check the name.
            text = ""
        findings.extend(scan(path, text, terms))

    for finding in findings:
        where = f"{finding.path}:{finding.line}" if finding.line else f"{finding.path} (its name)"
        sys.stderr.write(
            f"{where}: matches line {finding.term_line} of your sensitive terms file\n"
        )
    if findings:
        sys.stderr.write("Remove the private detail, then commit again.\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
