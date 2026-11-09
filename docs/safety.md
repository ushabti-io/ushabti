# Safety

An ushabti (one agent instance) can be given tools that change real systems.
This page lists every rule that stands between a model's proposal and an
action, every default, and exactly how to loosen each one. The terms come from
the [glossary](glossary.md).

The short version: out of the box an ushabti changes nothing. It observes,
records what it would do, and stops there. Letting it act takes several
separate, deliberate changes, and each one is listed under
[Letting an ushabti act](#letting-an-ushabti-act).

## The weighing

The weighing is the safety gate every write implement must pass. An implement
is a tool an ushabti can use, and each one is either `read` or `write`. When a
model proposes an implement call, the call is weighed before anything happens.

The default weighing applies these rules in order, and the first one that
matches decides the verdict. The rules read two things: the spell (the
declarative definition of the ushabti), and whether the ushabti is halted
(stopped from acting by the kill switch). Each verdict is written to Papyrus
(the append-only audit log).

| # | When | Verdict | `rule` in Papyrus |
|---|---|---|---|
| 1 | The ushabti is halted. | `REJECT` | `halted` |
| 2 | The implement is not listed in the spell. | `REJECT` | `not_listed` |
| 3 | The implement is `read`. | `PASS` | `read` |
| 4 | The spell has `dry_run: true`. | `DRY_RUN` | `dry_run` |
| 5 | The implement or its kind is listed in `require_approval_for`. | `NEEDS_APPROVAL` | `approval_required` |
| 6 | The write would exceed `max_actions_per_hour`. | `REJECT` | `rate_limited` |
| 7 | Otherwise. | `PASS` | `allowed` |

Every verdict carries a reason in plain language, and both are written to
Papyrus before the call runs.

The order matters. A halt beats everything, including reads. An unlisted
implement is rejected before its kind is even considered. A dry run beats
approval and the hourly limit, so while `dry_run` is true the other two settings
have no effect.

What each verdict does:

| Verdict | What happens |
|---|---|
| `PASS` | The implement runs. Its result or its failure is recorded. |
| `DRY_RUN` | The call is recorded and not run. |
| `NEEDS_APPROVAL` | The call is recorded and not run. The summons (the unit of work) ends in the state `escalated`, visible at `GET /summons/{id}`. |
| `REJECT` | The call is recorded with the reason and not run. |

`NEEDS_APPROVAL` is a dead end in this scaffold: there is no way to approve a
call and have it run. It marks the place where an approval workflow will go.

### The guard behind the gate

The weighing is not the only check. Every implement inherits a `run` method
that refuses a write unless it is handed a `PASS` verdict naming that exact
implement. A caller that skipped the weighing, or a custom weighing that
returned the wrong verdict, hits that guard and the write does not happen. This
is what "no code path runs a write implement without a verdict" means in
practice, and a test proves it for every registered write implement.

The vigil (the monitoring loop) observes through read implements without a
verdict. The same guard means it cannot run a write that way.

## Defaults, and how to loosen each one

| Default | What it protects | How to loosen it |
|---|---|---|
| `dry_run: true` | No write implement ever runs. | Set `spec.weighing.dry_run: false` in the spell. |
| `require_approval_for: [write]` | Every write needs approval, so none runs even with `dry_run` off. | Set `spec.weighing.require_approval_for: []`, or list only the implements that should still need approval. |
| `max_actions_per_hour: 5` | At most 5 writes run in any one hour. | Raise `spec.weighing.max_actions_per_hour`. `0` allows none. |
| A spell can only use the implements it lists. | A model cannot reach for a tool the spell did not grant. | Add the implement's name to `spec.implements`. |
| Kubernetes RBAC is read-only. | The cluster refuses writes whatever the ushabti attempts. | Set `agent.rbac.allowWrite: true` in the Helm values and list the namespaces in `agent.rbac.writeNamespaces`. Write verbs are granted in those namespaces only, never cluster-wide. |
| No admin token is set, so summons, halt, and resume are disabled. | Nobody on the network can hand the ushabti work. | Set `USHABTI_ADMIN_TOKEN`, from a Secret. Callers must then present it. |
| The HTTP server binds to `127.0.0.1`. | Only the same host can reach it. | Set `USHABTI_HOST=0.0.0.0`. The container images do this. |
| The Helm NetworkPolicy allows no ingress to an ushabti. | Other pods cannot reach its API. | Add peers to `networkPolicy.ingressFrom`. In overseen mode the overseer (the optional orchestrator) is allowed already. |
| The container runs as a non-root user with a read-only root filesystem. | A compromised process cannot rewrite its own image. | Not configurable in the chart. Change the Deployment if you must. |
| Papyrus is kept on a PersistentVolumeClaim. | A rescheduled pod keeps its audit log and its halt state. | Set `agent.persistence.enabled: false` to use an `emptyDir`. Read [Halt](#halt) first. |

Each default is independent. Loosening one does not loosen another.

### What cannot be loosened

These are not settings.

- **Every write is weighed.** There is no flag that skips the weighing.
- **Papyrus is append-only.** The file is only ever opened for appending or for
  reading. Nothing rewrites, truncates, or deletes it.
- **There is no shell implement.** An ushabti cannot run arbitrary commands.
- **Secrets never live in a spell.** A key named like a secret may only hold an
  environment reference such as `${ANTHROPIC_API_KEY}`. A literal value is
  rejected when the spell is loaded.
- **Halt needs no model.** See below.

## Halt

Halt is the kill switch. A halted ushabti stops acting but keeps observing.

To halt one ushabti:

```bash
ushabti halt --url http://127.0.0.1:8080
# or
curl -X POST http://127.0.0.1:8080/halt -H "Authorization: Bearer $USHABTI_ADMIN_TOKEN"
```

To halt everything an overseer supervises, point the same command at the
overseer. It tells every ushabti, including ones it has
marked lost, and returns an error naming any it could not reach.

```bash
ushabti halt --url http://127.0.0.1:8081
```

What halt does:

- It takes effect immediately and makes no model call.
- Rule 1 of the weighing rejects every call from then on, reads included, so a
  summons already being answered cannot act.
- The vigil keeps ticking. Isfet (current drift from the desired state) and
  omens (predictions that it will drift soon) are still recorded, but they
  raise no summons and no model is called.
- A summons that arrives while halted waits in the queue.

Halt survives a restart. The state is the last `halt` or `resume` line in
Papyrus, replayed at startup. The hourly action count is rebuilt the same way,
so a restart does not reset the limit either. Both depend on Papyrus outliving
the process: with `agent.persistence.enabled: false`, a rescheduled pod starts
with an empty log and is no longer halted.

To resume:

```bash
ushabti resume --url http://127.0.0.1:8080
```

Resume is per ushabti on purpose. An overseer fans out halt but not resume, so
bringing the workforce (everything deployed) back is something you do one
ushabti at a time.

The halt endpoints need the admin token, like every endpoint that makes an
ushabti do something. If no token is set they are disabled, and the only way to
stop an ushabti is to stop its process.

## The admin token

`USHABTI_ADMIN_TOKEN` is one shared bearer token. Only `/healthz` and
`/metrics` answer without it.

- An operator presents it to submit a summons, to halt, and to resume.
- An ushabti presents it to register with an overseer.
- An overseer presents it to route a summons to an ushabti and to fan out a halt.

Registration is guarded for a reason: the overseer sends the token to whatever
URL an ushabti registered, so that URL must come from a peer that already holds
the token.

Guarding summons is stricter than it may look necessary. A summons puts the
caller's words in front of the model, and once `dry_run` is off those words can
lead to a write. An open summons endpoint would let anyone who can reach the
port steer the ushabti.

Supply the token from the environment or from a Kubernetes Secret. Never write
it into a file in the repository.

## Papyrus

Each line of Papyrus has a timestamp, the name of the ushabti, the event, a
payload, and the verdict when there was one.

The spell sets where it is written, under `spec.papyrus.path`.
`USHABTI_PAPYRUS_PATH` overrides that and is never second-guessed: if it cannot
be written, the ushabti refuses to start. If the spell's own path cannot be
written, which is normal for a path such as `/var/lib/ushabti` on a
workstation, the ushabti falls back to
`~/.local/state/ushabti/<spell>/papyrus.jsonl` and logs a warning naming both
paths. In a container with a read-only root filesystem that fallback is not
writable either, so the ushabti stops instead of running without an audit log.

## Letting an ushabti act

Each of these is a separate decision. An ushabti acts only when all of them
have been made.

1. List the write implement under `spec.implements`.
2. Set `spec.weighing.dry_run: false`.
3. Remove the implement, and `write`, from `spec.weighing.require_approval_for`.
4. Check that `spec.weighing.max_actions_per_hour` is what you want.
5. On Kubernetes, set `agent.rbac.allowWrite: true` and list the namespaces it
   may change in `agent.rbac.writeNamespaces`.
6. Make sure Papyrus is on persistent storage, so a halt survives a restart.
7. Set an admin token, so the ushabti can be halted.

Before step 2, run with `dry_run: true` for a while and read Papyrus. Every
`DRY_RUN` verdict is something the ushabti would have done.

## What this does not protect against

- **A model steered by what it reads.** Observations and implement results go
  into the model's context. A hostile log line or resource name could try to
  steer it. The weighing limits the damage to the implements the spell lists,
  at the rate the spell allows, but it does not judge whether an allowed
  action is wise. Approval is the control for that, and approval is not built
  yet.
- **A holder of the admin token.** The token is shared and all-powerful within
  a workforce. Anyone who has it can submit a summons.
- **A hostile spell.** The person who writes the spell decides what the ushabti
  may do. Review spells the way you review code.
- **A wider patch than a restart.** Kubernetes cannot grant "restart only". The
  opt-in `patch` verb on deployments lets an ushabti change any part of a
  deployment in the namespaces you list. The implements only restart and scale,
  but the permission itself is broader. An admission policy that limits what the
  ushabti's ServiceAccount may patch would close that gap, and is not built.

Report a vulnerability as described in [SECURITY.md](../SECURITY.md).
