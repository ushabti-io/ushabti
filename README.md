# Ushabti

[![CI](https://github.com/ushabti-io/ushabti/actions/workflows/ci.yaml/badge.svg)](https://github.com/ushabti-io/ushabti/actions/workflows/ci.yaml)

Ushabti is an open-source DevOps agent harness. It monitors infrastructure,
detects current failures, predicts upcoming ones, and takes guarded corrective
actions. Each agent watches the systems you point it at, compares what it sees
to a description of healthy, and asks a model what to do when the two differ.
Nothing the model proposes runs until it has passed a safety gate, and by
default nothing runs at all: an agent records what it would have done. It works
with Anthropic models and with any OpenAI-compatible endpoint, which covers
local servers such as Ollama and vLLM. The first target is a homelab: K3s on
Proxmox, with Prometheus metrics.

> **Status: scaffold.** The interfaces, data models, wiring, safety gate, audit
> log, and tests are real. The Kubernetes, Proxmox, and Prometheus integrations
> are declared stubs that raise `NotImplementedError`. See [Status](#status).

## The name

Ushabti were ancient Egyptian funerary figurines placed in tombs to do labour on
behalf of the deceased. The name is associated with the Egyptian word for
"answer," so they were known as "The Answerers." When the deceased was summoned
to work, the figurine was to answer "Here am I" and take their place. The most
elaborate tombs held 365 workers and 36 overseers, each overseer supervising ten
workers.

The variant spellings are **shabti** and **shawabti**. If you searched for
either, this is the same project.

The vocabulary comes from there. An ushabti is one agent instance. A spell is
the file that defines it. A summons is a unit of work. Every term is in the
[glossary](docs/glossary.md), each with a plain meaning.

## Three modes

Ushabti is one tool that runs in three topologies. Two more terms describe
them. An overseer is the optional orchestrator: it registers ushabti, routes
each summons to one that can take it, and tracks their health. A decade is a
group of ushabti supervised by one overseer, ten by default.

| Mode | Meaning |
|---|---|
| `solo` | One ushabti running one spell; no overseer. |
| `independent` | Several ushabti, each configured and deployed separately; no overseer. |
| `overseen` | One or more overseers coordinate decades of ushabti. |

## Quick start

This runs one ushabti in `solo` mode with Docker Compose and the fake model. It
needs no API key and no cluster, and it changes nothing.

```bash
export USHABTI_ADMIN_TOKEN="$(openssl rand -hex 32)"
docker compose -f deploy/compose/solo.yaml up --build --detach
```

The ushabti is now keeping its vigil (the monitoring loop) over the example
spell [`examples/spells/k3s-watcher.yaml`](examples/spells/k3s-watcher.yaml).
Ask how it is:

```bash
curl http://127.0.0.1:8080/healthz
```

The answer is its "Here am I" message (the health signal): name, spell, mode,
whether it is halted (stopped from acting by the kill switch), and when it last
kept its vigil.

Now give it a summons. The token you exported guards every endpoint that makes
an ushabti do something.

```bash
curl -X POST http://127.0.0.1:8080/summons \
  -H "Authorization: Bearer $USHABTI_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source": "operator", "reason": "The web deployment is not ready."}'
```

The fake model answers from [`examples/fake-script.yaml`](examples/fake-script.yaml)
and proposes restarting a deployment. That is a write implement (a tool that can
change something), so it goes through the weighing (the safety gate). The spell
has `dry_run: true`, so the verdict is `DRY_RUN`: recorded, not run. Read it in
Papyrus (the append-only audit log):

```bash
docker compose -f deploy/compose/solo.yaml exec agent cat /var/lib/ushabti/papyrus.jsonl
```

Look for the line with `"event":"verdict"` and `"verdict":"DRY_RUN"`.

Halt stops an ushabti from acting at once. It keeps observing.

```bash
curl -X POST http://127.0.0.1:8080/halt -H "Authorization: Bearer $USHABTI_ADMIN_TOKEN"
```

Stop it when you are done:

```bash
docker compose -f deploy/compose/solo.yaml down
```

If port 8080 is taken, set `USHABTI_HOST_PORT` to another port before `up`.

### Without Docker

With [uv](https://docs.astral.sh/uv/) installed:

```bash
uv sync --all-packages
uv run ushabti check-spell examples/spells/k3s-watcher.yaml
uv run ushabti run --spell examples/spells/k3s-watcher.yaml --model fake --once
```

`check-spell` validates a spell and prints it with secrets masked. `run --once`
keeps one vigil tick and exits.

### Overseen mode

[`deploy/compose/overseen.yaml`](deploy/compose/overseen.yaml) runs an overseer
with two ushabti. Post a summons to the overseer on port 8081 and it routes it
to the ushabti whose spell covers the field (the target) and implement named in
the summons.

### Kubernetes

The Helm chart is in [`deploy/helm/ushabti`](deploy/helm/ushabti). Its
[`values.yaml`](deploy/helm/ushabti/values.yaml) explains each setting.

## Safety defaults

These hold out of the box. Each one must be loosened on purpose, and
[docs/safety.md](docs/safety.md) says exactly how.

- **`dry_run: true` by default** in every spell and in every Helm and Compose
  example. An ushabti records what it would do and changes nothing.
- **Every write implement passes through the weighing.** There is no code path
  that runs a write implement without a verdict.
- **A spell can only use the implements it lists.** Anything else is rejected.
- **There is no shell implement.**
- **Kubernetes RBAC is read-only by default.** Write verbs need
  `agent.rbac.allowWrite: true`, and are then granted only in the namespaces
  you list.
- **Papyrus is append-only.** Nothing rewrites or truncates it.
- **Halt works without the model.** `ushabti halt` and `POST /halt` need no
  model call and take effect immediately.
- **Secrets never live in files.** A spell may only refer to them as
  environment references such as `${ANTHROPIC_API_KEY}`.
- **Only `/healthz` and `/metrics` answer without the admin token.** Summons,
  halt, resume, and overseer registration all need `USHABTI_ADMIN_TOKEN`, and
  are disabled when it is unset.

## Documentation

- [Glossary](docs/glossary.md): the shared vocabulary.
- [Architecture](docs/architecture.md): how the pieces fit, and each step of the loop.
- [Safety](docs/safety.md): every rule of the weighing, every default, and how to loosen each.
- [Spells](docs/spells.md): the `spell.yaml` reference.
- [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), and the
  [Code of Conduct](CODE_OF_CONDUCT.md).

## Packages

| Package | What it is | Command | Distributed as |
|---|---|---|---|
| [`ushabti`](packages/ushabti) | The SDK: spells, implements, the weighing, Papyrus, model providers. | | PyPI package `ushabti` |
| [`ushabti-agent`](packages/ushabti-agent) | The runtime of one ushabti, with its HTTP API. | `ushabti` | Container image `ghcr.io/ushabti-io/ushabti-agent` |
| [`ushabti-overseer`](packages/ushabti-overseer) | The overseer. | `ushabti-overseer` | Container image `ghcr.io/ushabti-io/ushabti-overseer` |

Only the SDK goes to PyPI. The agent and the overseer are services, so they ship
as container images.

## Status

This repository is a scaffold. Nothing is published to PyPI or to a container
registry yet.

What is implemented and tested: spell loading and validation, the weighing,
Papyrus, the vigil loop, summons handling, halt and resume, the overseer's
registry, decades and routing, the fake model provider, and minimal Anthropic
and OpenAI-compatible providers.

What is a stub: the `kubernetes.*`, `proxmox.*`, and `prometheus.*` implements.
Each is declared with its kind and its parameters, and raises
`NotImplementedError`. Until they are built, an ushabti pointed at a real
cluster can be summoned and will reason, but cannot observe or act.

The container images will be published to `ghcr.io/ushabti-io` by the release
workflow. None exists until the first release, so for now build them locally,
as the quick start does.

## License

[Apache-2.0](LICENSE).
