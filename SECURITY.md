# Security

Ushabti can be given tools that change real infrastructure, so security reports
are taken seriously. Thank you for making one.

## Reporting a vulnerability

Please do not open a public issue for a vulnerability.

Report it privately through GitHub. Open the repository's
[Security tab](https://github.com/ushabti-io/ushabti/security) and choose
"Report a vulnerability", or go straight to the
[private report form](https://github.com/ushabti-io/ushabti/security/advisories/new).
Only the maintainers can see what you send.

Include what you can of the following:

- what the problem is and what an attacker could do with it;
- the version or commit you tested;
- steps to reproduce it, or a proof of concept;
- any spell (the file that defines an agent) or configuration needed to
  trigger it, with secrets removed.

You should get an acknowledgement within a few days. Please give the maintainers
a reasonable time to fix the problem before you make it public. You will be
credited in the release notes unless you ask not to be.

## What counts

These are the properties the project promises. A way to break any of them is a
vulnerability. The terms come from the [glossary](docs/glossary.md).

- A write implement (a tool that can change something) running without a `PASS`
  verdict from the weighing (the safety gate).
- An ushabti (an agent instance) using an implement its spell (its declarative
  definition) does not list.
- A halted ushabti (one stopped by the kill switch) acting, or a halt being
  undone without a resume.
- An entry in Papyrus (the append-only audit log) being changed or removed, or
  an action that leaves no entry.
- A secret being written to a file, to a log, or to an error message.
- An endpoint other than `/healthz` and `/metrics` answering without the admin
  token.
- The admin token being sent anywhere other than an authenticated peer.

[docs/safety.md](docs/safety.md) describes the safety model in full, including
what it does not protect against.

## What does not count

- A spell that grants an ushabti dangerous implements and turns `dry_run` off.
  The author of a spell decides what the ushabti may do.
- Anything that needs the admin token. The token is all-powerful within a
  workforce (everything deployed) by design.
- The Kubernetes and Proxmox implements not working. They are declared stubs
  in this scaffold.

## Supported versions

The project is pre-release. Only the latest commit on `main` receives fixes.
