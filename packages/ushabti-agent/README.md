# ushabti-agent

The agent runtime for [Ushabti](https://github.com/ushabti-io/ushabti), an open-source DevOps agent harness.

One ushabti (one agent instance) runs one spell (its declarative definition). It keeps a vigil
(the monitoring loop) over its fields (the targets it tends), and every write implement (tool) it
wants to use must pass the weighing (the safety gate) first.

```bash
pip install ushabti-agent

ushabti check-spell spell.yaml
ushabti run --spell spell.yaml --model fake --once
```

Spells default to `dry_run: true`. An ushabti records what it would do and changes nothing until
you loosen that on purpose.

See the repository README for the glossary, the architecture, and the safety defaults.
