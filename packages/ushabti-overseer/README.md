# ushabti-overseer

The overseer for [Ushabti](https://github.com/ushabti-io/ushabti), an open-source DevOps agent harness.

The overseer is the optional orchestrator. It registers ushabti (agent instances), groups them into
decades (groups of ten by default), tracks their "Here am I" messages (the health signal each
ushabti sends), and routes each summons (a unit of work) to an ushabti whose spell (its
declarative definition) covers it.

It ships as a container image, `ghcr.io/ushabti-io/ushabti-overseer`. It is not published to PyPI.

```bash
export USHABTI_ADMIN_TOKEN="$(openssl rand -hex 32)"
docker run --rm -p 127.0.0.1:8081:8081 -e USHABTI_ADMIN_TOKEN \
  ghcr.io/ushabti-io/ushabti-overseer:<version>
```

The overseer and every ushabti share that token. An ushabti presents it to register, and the
overseer presents it to route a summons or to fan out a halt (the kill switch).

Until the first release there is no published image. Build one from the repository root:

```bash
docker build -f deploy/docker/overseer.Dockerfile -t ushabti-overseer:dev .
```

To run it from a checkout instead of a container, use `uv run ushabti-overseer run`.

You only need an overseer in `overseen` mode. In `solo` and `independent` modes each ushabti runs
on its own.

See the repository README for the glossary, the architecture, and the safety defaults.
