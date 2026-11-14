# Changelog

Notable changes to Ushabti, newest first. The three packages share one version,
and a release is a `v*` tag pushed by a maintainer; [CONTRIBUTING.md](CONTRIBUTING.md)
has the steps. Entries name the pull request that made the change.

## Unreleased

Nothing yet.

## 0.1.0.dev2 (2026-11-13)

The vigil (the monitoring loop) can be watched without a shell into the pod.

- `GET /papyrus` reads Papyrus (the audit log) as NDJSON, oldest first, with
  `event`, `since`, `summons`, `assertion`, and `limit` filters. `GET /summons`
  lists the summonses an ushabti remembers, newest first. Both need the admin
  token. Nothing new can write to Papyrus (#24).
- Every Papyrus event except ticks, halt, and resume also goes to the process
  log as a trimmed structured line at `info`; isfet (drift now) and a summons
  that fails or is escalated log at `warning`; a tick logs its counts. No
  observation data, call arguments, or action results are logged (#27).
- Helm: `metrics.serviceMonitor.enabled` renders a ServiceMonitor for the
  ushabti Services and lets the Prometheus namespace through the NetworkPolicy
  (#26).

## 0.1.0.dev1 (2026-11-13)

The first release with implements (tools) that reach real systems.

- `prometheus.query` and `prometheus.query_range` over the Prometheus HTTP
  API, with results shaped for Ma'at (desired state) conditions: `value` for
  one sample, `first`, `last`, `min`, and `max` for one series (#4).
- `kubernetes.list_pods` and `kubernetes.get_deployment` through the Kubernetes
  client's async API. A field's `namespaces` bound every call, and a new
  `context` key names a kubeconfig context for runs outside the cluster.
  Results expose `not_ready` and `missing` for Ma'at conditions (#14).
- Example spells `metrics-watcher.yaml` and `workloads-watcher.yaml`.
- The agent image installs the `prometheus` and `kubernetes` extras.
- Repository: `AGENTS.md` and a `CLAUDE.md` that imports it (#3); secret and
  private-detail scanning in pre-commit and CI (#13); a ruleset on `main` that
  requires a pull request and the `All checks` status, and lets only an admin
  create a `v*` tag (#15).

## 0.1.0.dev0 (2026-11-09)

The scaffold. Spells, implements, the weighing (the safety gate), Papyrus, and
the fake, Anthropic, and OpenAI-compatible model providers in the SDK; the
agent runtime with its CLI and HTTP API; the overseer with its registry,
decades, and routing; Dockerfiles, Compose files, and the Helm chart; the docs;
CI and release workflows. Every Kubernetes, Proxmox, and Prometheus implement
was a declared stub. Only the SDK is published to PyPI; the agent and the
overseer ship as container images.
