# ushabti-overseer

The overseer for [Ushabti](https://github.com/ushabti-io/ushabti), an open-source DevOps agent harness.

The overseer is the optional orchestrator. It registers ushabti (agent instances), groups them into
decades (groups of ten by default), tracks their "Here am I" messages (the health signal each
ushabti sends), and routes each summons (a unit of work) to an ushabti whose spell (its
declarative definition) covers it.

```bash
pip install ushabti-overseer

export USHABTI_ADMIN_TOKEN="$(openssl rand -hex 32)"
ushabti-overseer run
```

The overseer and every ushabti share that token. An ushabti presents it to register, and the
overseer presents it to route a summons or to fan out a halt (the kill switch).

You only need an overseer in `overseen` mode. In `solo` and `independent` modes each ushabti runs
on its own.

See the repository README for the glossary, the architecture, and the safety defaults.
