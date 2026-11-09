# AGENTS.md

Instructions for coding agents working in this repository. People should read
[CONTRIBUTING.md](CONTRIBUTING.md), which covers the same ground at more length.

## What this is

Ushabti is a DevOps agent harness: agents that watch infrastructure, compare it
to a description of healthy, and take guarded corrective actions. The safety
model is the point of the project. Read [docs/safety.md](docs/safety.md) before
changing anything near it.

It is a uv workspace of three Python packages, on Python 3.12 or newer.

| Path | What it holds | Shipped as |
|---|---|---|
| `packages/ushabti` | The SDK: spells (agent definitions), implements (tools), the weighing (the safety gate), Papyrus (the audit log), model providers | PyPI package `ushabti` |
| `packages/ushabti-agent` | The runtime of one agent, the `ushabti` command, its HTTP API | Container image |
| `packages/ushabti-overseer` | The optional orchestrator and the `ushabti-overseer` command | Container image |

`deploy/` holds the Dockerfiles, the Compose files, and the Helm chart. `docs/`
holds the glossary, the architecture, the safety model, and the spell reference.

## Commands

```bash
uv sync --all-packages     # set up
uv run ruff check .        # lint
uv run ruff format .       # format; CI runs it with --check
uv run mypy packages       # type-check, strict
uv run pytest              # every test
uv run pytest packages/ushabti/tests/test_weighing.py -k order   # one file, one test
```

Run all four checks before you call a change done. If you touched `deploy/`:

```bash
helm lint deploy/helm/ushabti
docker compose -f deploy/compose/solo.yaml config --quiet
docker build -f deploy/docker/agent.Dockerfile -t ushabti-agent:dev .
docker build -f deploy/docker/overseer.Dockerfile -t ushabti-overseer:dev .
```

## Vocabulary

The project has its own terms and uses them exactly: in identifiers, commands,
config keys, and docs. [docs/glossary.md](docs/glossary.md) has the full list.

| Write | Never | Plain meaning |
|---|---|---|
| `ushabti` (same in the plural) | worker | one agent instance running one spell |
| `overseer` | orchestrator, controller | the optional orchestrator |
| `decade`, `workforce` | group, fleet | a group of ushabti under one overseer; everything deployed |
| `spell` | config, manifest | the declarative definition of one ushabti |
| `implement` | tool, plugin | a tool an ushabti can use, either `read` or `write` |
| `field` | target, resource | a target an ushabti tends |
| `summons` (plural `summonses`) | task, job | a unit of work |
| `here_am_i`, `HereAmI` | heartbeat | the health signal and acknowledgement |
| `vigil` | poll loop | the monitoring loop |
| `maat`, `Maat` | desired state | the desired state. Write "Ma'at" in prose |
| `isfet`, `omen` | failure, warning | something is wrong now; something will be wrong soon |
| `weighing`, `Verdict` | policy check | the safety gate every write implement must pass |
| `papyrus` | audit log | the append-only audit log |
| `halt` | pause | the kill switch |

The plain meanings belong in prose, not in names. In docs, the first use of each
term on a page carries its plain meaning, for example "a summons (a unit of
work)". The model-facing types, such as `ToolCall` and `tool_calls`, keep the
word "tool", because that is what model APIs call it.

## Safety invariants

Do not weaken any of these without the maintainer saying so, not even to make a
test pass. If a change touches one, say which and why.

1. Every write implement passes through the weighing. `Implement.run` refuses a
   write without a `PASS` verdict that names it. Implement `_run`. Never
   override or bypass `run`.
2. `dry_run` defaults to true, in `WeighingPolicy` and in every spell under
   `examples/` and `deploy/`.
3. A spell can only use the implements it lists. There is no shell implement.
   Do not add one.
4. Papyrus is append-only. `JsonlPapyrus` opens with `O_APPEND` and has no way
   to rewrite, truncate, or delete.
5. A verdict is written to Papyrus before the action it allows.
6. Halt makes no model call. Halt state and the hourly write count are rebuilt
   from Papyrus at startup.
7. Only `/healthz` and `/metrics` answer without `USHABTI_ADMIN_TOKEN`. That
   covers summons, halt, resume, and registration with an overseer.
8. Secrets never go in a file. A spell holds `${VAR}` references only, and no
   error message echoes a secret's value.
9. The Helm ClusterRole is read-only. Write verbs exist only in namespaced
   Roles, behind both `agent.rbac.allowWrite` and `agent.rbac.writeNamespaces`.

## Code conventions

- Add dependencies with `uv add`. Never write a version into a `pyproject.toml`
  by hand, and never pin one from memory.
- Before writing against a third-party SDK, read its current documentation. The
  Anthropic and OpenAI SDKs have both changed major versions recently.
- Optional SDKs (`anthropic`, `openai`, `kubernetes`, `proxmoxer`, `httpx`) are
  imported inside functions, never at the top of a module. The SDK must import
  with no extras installed, and `test_provider_imports.py` checks that.
- Subclass `StrictModel` for anything a person writes by hand (unknown keys are
  errors) and `WireModel` for anything sent between processes or written to
  Papyrus (unknown keys are ignored).
- Describe a model's keys with attribute docstrings. They become the
  descriptions in [docs/spells.md](docs/spells.md).
- Never hard-code a model ID. The one exception is the Anthropic provider's
  default in `models/anthropic.py`.
- Nine implements under `implements/` are scaffold stubs, marked
  `# TODO(scaffold):` and raising `NotImplementedError`. To build one, keep its
  `name`, `kind`, and `parameters`, write `_run`, and delete the marker.
- `docs/spells.md` is generated. Do not edit it. After changing a model or an
  implement, run `uv run python -m ushabti.reference > docs/spells.md`. A test
  fails when it is stale.

## Tests

- Test files have names unique across the whole repository, such as
  `test_agent_runtime.py`, because mypy checks every package in one run.
- There is no `conftest.py`. Shared helpers live in a `*_testkit.py` beside the
  tests.
- No network, no real model, no real cluster. Use `FakeProvider` and the
  `noop.read` and `noop.write` implements.
- No real secrets, model IDs, hostnames, or IP addresses. Use `test-model` and
  hosts under `example.invalid`.
- A FastAPI test client only parses a body sent as JSON. Pass `json=`, or set
  `Content-Type: application/json`.

## Writing

- Plain, direct prose. Short sentences.
- No em-dashes, in docs, comments, or commit messages. Restructure the sentence.
- In Mermaid diagrams, break lines in a label with `<br/>`, never `\n`.

## Commits and releases

- Use Conventional Commits. Types: `feat`, `fix`, `docs`, `test`, `refactor`,
  `chore`, `ci`. Scopes: `sdk`, `agent`, `overseer`, `deploy`.
- Never push a tag and never publish. A maintainer releases by pushing a `v*`
  tag. `release.yaml` then publishes the SDK to PyPI behind an approval and
  pushes both images.
- Only `ushabti` goes to PyPI. The agent and overseer packages carry the
  `Private :: Do Not Upload` classifier. Leave it there.
- The three packages share one version.
- GitHub Actions are pinned to commit SHAs. The uv version lives in one place,
  the `FROM ghcr.io/astral-sh/uv:` line of the Dockerfiles, and the workflows
  read it from there. Both Dockerfiles must have identical `FROM` lines.

## SCAFFOLD.md is history

`SCAFFOLD.md` is the instruction this repository was first built from. Where it
disagrees with the code, the code is right. These differences are deliberate.
Do not change them back.

- Only the SDK is published to PyPI. The document says all three packages are.
- Every endpoint but `/healthz` and `/metrics` needs the admin token. The
  document guards only halt and resume.
- Write RBAC needs an explicit list of namespaces. The document has one switch.
- `OWNER` is `ushabti-io`, and the Anthropic provider has a default model.
- `Implement` is an abstract base class, not a protocol, so that every
  implement inherits the verdict check.

Three tests compare the glossary, the diagrams, and the example spell with
`SCAFFOLD.md`. They skip themselves if the file is removed.

## Known gaps

- The `kubernetes.*`, `proxmox.*`, and `prometheus.*` implements are stubs.
- `NEEDS_APPROVAL` is recorded and exposed, and nothing more. There is no way
  to approve a call yet.
- Before any spell runs with `dry_run: false` against a real cluster, the
  admission policy in issue #2 has to exist.
