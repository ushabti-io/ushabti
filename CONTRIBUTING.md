# Contributing

Thanks for helping with Ushabti. This page covers setup, the checks every
change must pass, commit conventions, and how to add an implement (a tool an
agent can use). Please also read the [Code of Conduct](CODE_OF_CONDUCT.md).

## Vocabulary

The project has its own terms, and they are used exactly: in identifiers,
commands, config keys, and docs. An ushabti is one agent instance, a spell is
its declarative definition, and so on through the [glossary](docs/glossary.md).
Two rules follow from it:

- Use the glossary term, not a synonym. Write `summons`, not "task". Write
  `here_am_i`, not "heartbeat". Write `overseer`, not "orchestrator". Write
  `papyrus`, not "audit log", in an identifier.
- In docs, the first use of each term on a page includes its plain meaning, for
  example "a summons (a unit of work)". Write "Ma'at" in prose and `Maat` or
  `maat` in code.

## Setup

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or newer. Docker and
Helm are only needed for the container and chart checks.

```bash
uv sync --all-packages
uv run pre-commit install
```

The repository is a uv workspace with three packages under `packages/`:
`ushabti` (the SDK), `ushabti-agent`, and `ushabti-overseer`. `uv sync` installs
all three in editable mode, along with the development tools.

## Checks

Run these before you open a pull request. Continuous integration runs the same
ones.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy packages
uv run pytest --cov
```

To format instead of checking, run `uv run ruff format .`.

If you touched the deployment files:

```bash
helm lint deploy/helm/ushabti
docker compose -f deploy/compose/solo.yaml config --quiet
docker build -f deploy/docker/agent.Dockerfile -t ushabti-agent:dev .
docker build -f deploy/docker/overseer.Dockerfile -t ushabti-overseer:dev .
```

### Tests

- Test files have names that are unique across the whole repository, such as
  `test_agent_runtime.py`, because mypy checks every package in one run.
- There is no `conftest.py`. Shared helpers live in a testkit module beside the
  tests, such as `agent_testkit.py`.
- Tests never use the network, a real model, or a real cluster. Use
  `FakeProvider` and the `noop.read` and `noop.write` implements.
- Never put a real secret, model ID, hostname, or IP address in a test. Use
  names such as `test-model` and hosts under `example.invalid`.

### Dependencies

Add a dependency with `uv add`, so the lock file records the version that
resolved. Do not write a version into `pyproject.toml` by hand.

```bash
uv add --package ushabti-agent some-package
uv add --package ushabti --optional some-extra some-package
uv add --dev some-tool
```

### Generated docs

[docs/spells.md](docs/spells.md) is generated from the `Spell` model, and a
test fails when the two differ. After changing a model, the description of one
of its keys, or an implement, regenerate it:

```bash
uv run python -m ushabti.reference > docs/spells.md
```

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/). The type says
what kind of change it is, and the optional scope says where.

```text
feat(agent): add a resume command
fix(sdk): reject a spell with two fields of the same name
docs: explain how halt survives a restart
chore(deps): update the lock file
```

Types in use: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `ci`.
Scopes in use: `sdk`, `agent`, `overseer`, `deploy`, `scaffold`.

Keep the subject in the imperative and under 72 characters. Use the body to say
why the change was made.

## Adding an implement

Every implement is either `read` or `write`, and the difference matters: a
write implement only runs after the weighing (the safety gate) returns `PASS`.

1. **Write the class.** Subclass `Implement` in the module for its system under
   `packages/ushabti/src/ushabti/implements/`. Set `name`, `kind`,
   `description`, and `parameters`, and implement `_run`.

   ```python
   from collections.abc import Mapping
   from typing import ClassVar

   from pydantic import JsonValue

   from ushabti.field import Field
   from ushabti.implements import Implement, ImplementKind


   class NodeUptime(Implement):
       name: ClassVar[str] = "proxmox.node_uptime"
       kind: ClassVar[ImplementKind] = ImplementKind.READ
       description: ClassVar[str] = "Get how long a Proxmox node has been up, in seconds."
       parameters: ClassVar[Mapping[str, JsonValue]] = {
           "type": "object",
           "properties": {"node": {"type": "string", "description": "Name of the node."}},
           "required": ["node"],
           "additionalProperties": False,
       }

       async def _run(self, arguments: Mapping[str, JsonValue], field: Field | None) -> JsonValue:
           node = arguments["node"]
           return {"node": node, "uptime_seconds": 0}
   ```

   - `name` is `namespace.operation` in lowercase, such as `proxmox.node_uptime`.
   - `kind` is `READ` if it only observes. If it can change anything at all, it
     is `WRITE`. When in doubt, it is `WRITE`.
   - `description` is what the model reads to decide whether to use it.
   - `parameters` is a JSON Schema for the arguments.
   - `field` is the field (the target) the call is aimed at, taken from the
     spell.
   - Implement `_run` only. Never override `run`: it holds the check that
     refuses a write without a `PASS` verdict.

2. **Register it.** Return an instance from the module's `implements()`
   function. For a new module, also add it to `builtin_implements()` in
   `implements/__init__.py`.

3. **Guard any third-party import.** If the implement needs an SDK, add it as an
   optional extra with `uv add --package ushabti --optional`, and import it
   inside `_run` or the constructor, never at the top of the module. Raise
   `MissingExtraError` when it is absent. The core must import without any
   extra installed, and a test checks that.

4. **Test it.** Cover what it returns, how it fails, and, for a write, that it
   refuses to run without a verdict. `test_implements.py` already checks the
   guard for every registered write implement, so a new one is covered as soon
   as it is registered.

5. **Regenerate the reference.** Run
   `uv run python -m ushabti.reference > docs/spells.md`.

6. **For a Kubernetes write,** check that the verbs it needs are in the
   opt-in block of `deploy/helm/ushabti/templates/rbac.yaml`.

To replace a scaffold stub, keep its `name`, `kind`, and `parameters`, write
the real `_run`, and delete the `# TODO(scaffold):` comment. The reference will
then list it as ready.

## Safety review

A change needs extra care if it touches any of these: the weighing, the guard
in `Implement.run`, Papyrus, halt (the kill switch), the admin token, or how a
spell is validated.
Read [docs/safety.md](docs/safety.md) first, and say in the pull request which
default the change affects and why it is still safe.

Never commit a secret. Secrets come from environment variables or Kubernetes
Secrets only.

## Releases

Nothing is published yet. The release workflow needs setup on PyPI before it
can run. The comment at the top of `.github/workflows/release.yaml` lists the
steps.

The three packages share one version and are released together. To cut a
release, set the same version in all three `pyproject.toml` files and in
`deploy/helm/ushabti/Chart.yaml`, and raise the `ushabti>=` bound in the agent
and overseer to match.
