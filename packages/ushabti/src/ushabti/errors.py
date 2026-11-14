"""Errors the SDK raises on purpose."""

from collections.abc import Iterable


class UshabtiError(Exception):
    """Base class for every error the SDK raises on purpose."""


class SpellError(UshabtiError, ValueError):
    """A spell failed validation. ``problems`` lists every reason found."""

    def __init__(self, problems: Iterable[str], *, source: str = "spell") -> None:
        self.problems = tuple(problems)
        self.source = source
        lines = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(f"{source} is not a valid spell:\n{lines}")


class UnresolvedEnvError(UshabtiError):
    """A value references environment variables that are not set."""

    def __init__(self, variables: Iterable[str]) -> None:
        self.variables = tuple(variables)
        names = ", ".join(f"${{{name}}}" for name in self.variables)
        super().__init__(f"environment variable not set: {names}")


class MissingExtraError(UshabtiError, ImportError):
    """An optional dependency is needed but its extra is not installed."""

    def __init__(self, extra: str, package: str) -> None:
        self.extra = extra
        self.package = package
        super().__init__(
            f'the "{package}" package is not installed; '
            f'install it with: pip install "ushabti[{extra}]"'
        )


class ModelError(UshabtiError):
    """A model provider is misconfigured or its request failed."""


class UnknownImplementError(UshabtiError, LookupError):
    """An implement name is not in the registry."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"{name} is not a registered implement")


class ImplementError(UshabtiError):
    """An implement could not do its work: bad arguments, no endpoint, or a failed call.

    The message is safe to record in Papyrus. It never carries a secret or a
    credential-bearing URL.
    """


class UnweighedWriteError(UshabtiError):
    """An implement was asked to run without a PASS verdict from the weighing.

    This is the guard behind the rule that no write implement runs without a
    verdict. Reaching it means a caller skipped the weighing.
    """
