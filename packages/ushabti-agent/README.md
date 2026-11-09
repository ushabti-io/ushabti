# ushabti-agent

The agent runtime for [Ushabti](https://github.com/ushabti-io/ushabti), an open-source DevOps agent harness.

One ushabti (one agent instance) runs one spell (its declarative definition). It keeps a vigil
(the monitoring loop) over its fields (the targets it tends), and every write implement (tool) it
wants to use must pass the weighing (the safety gate) first.

It ships as a container image, `ghcr.io/ushabti-io/ushabti-agent`. It is not published to PyPI.
The image's entrypoint is the `ushabti` command:

```bash
docker run --rm -v "$PWD/spell.yaml:/etc/ushabti/spell.yaml:ro" \
  ghcr.io/ushabti-io/ushabti-agent:<version> check-spell /etc/ushabti/spell.yaml
```

With no arguments, the image runs the spell mounted at `/etc/ushabti/spell.yaml` and serves its
HTTP API on port 8080. It writes Papyrus (the append-only audit log) under `/var/lib/ushabti`, so
mount a volume there.

To build the image yourself, from the repository root:

```bash
docker build -f deploy/docker/agent.Dockerfile -t ushabti-agent:dev .
```

To run the command from a checkout instead of a container:

```bash
uv sync --all-packages
uv run ushabti check-spell spell.yaml
uv run ushabti run --spell spell.yaml --model fake --once
```

Spells default to `dry_run: true`. An ushabti records what it would do and changes nothing until
you loosen that on purpose.

See the repository README for the glossary, the architecture, and the safety defaults.
