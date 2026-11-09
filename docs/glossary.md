# Glossary

This is the project's shared vocabulary. The README links to it, and `docs/glossary.md` is a copy of this section.

| Term | Identifier | Plain meaning | Origin |
|---|---|---|---|
| **Ushabti** | `Ushabti` | One agent instance running one spell. The plural is also "ushabti". | Historical: figurines that worked on behalf of the deceased. |
| **Overseer** | `Overseer` | The optional orchestrator. It registers ushabti, routes summons, and tracks their health. | Historical: overseer figures, each supervising ten workers. |
| **Decade** | `Decade` | A group of ushabti supervised by one overseer. The default size is 10 and is configurable. | The 1:10 ratio is historical; the word "decade" is ours. |
| **Workforce** | `Workforce` | Everything deployed: all overseers and ushabti. | Metaphor. |
| **Spell** | `Spell`, `spell.yaml` | The declarative definition of one ushabti: role, instructions, model, fields, implements, Ma'at, and weighing policy. | Historical: the shabti spell inscribed on the figurines. |
| **Implement** | `Implement` | A tool an ushabti can use. Every implement is either `read` or `write`. | Historical: figurines were carved holding tools such as a hoe, basket, or mattock. |
| **Field** | `Field` | A target an ushabti tends: a K3s cluster, a namespace, a Proxmox node, a service. | Historical: field labour was the figurines' main work. |
| **Summons** | `Summons` | A unit of work. It can come from the vigil, an overseer, or a human operator. | Historical: the figurine answered when the deceased was summoned to work. |
| **Here am I** | `HereAmI` | The heartbeat and acknowledgement message. It is also the body of the health endpoint. | Historical: the figurine's answer. |
| **Vigil** | `Vigil` | The monitoring loop: periodic observation of fields. | Metaphor. |
| **Ma'at** | `Maat` | The desired state: assertions about what healthy looks like. | Egyptian concept of order and balance. |
| **Isfet** | `Isfet` | Current drift from Ma'at: something is wrong now. | Egyptian concept of chaos, the opposite of ma'at. |
| **Omen** | `Omen` | A prediction that Ma'at will be broken soon, such as a disk filling or a certificate nearing expiry. | Metaphor. |
| **Weighing** | `Weighing`, `Verdict` | The safety gate every write implement must pass. Verdicts are `PASS`, `DRY_RUN`, `NEEDS_APPROVAL`, and `REJECT`. | Egyptian: the Weighing of the Heart against the feather of ma'at. |
| **Papyrus** | `Papyrus`, `PapyrusEntry` | The append-only audit log of omens, isfet, summons, verdicts, and actions. | Metaphor. |
| **Halt** | `halt` | The kill switch. A halted ushabti stops acting but keeps observing. | Plain term. |

**Naming rules:**
- Classes use PascalCase (`Ushabti`, `HereAmI`, `Maat`), modules use snake_case (`here_am_i.py`, `maat.py`), and config keys use snake_case.
- Write "Ma'at" in prose and `Maat` or `maat` in code, because identifiers cannot contain apostrophes.
- In docs, the first use of each term on a page includes its plain meaning, for example "a summons (a unit of work)".

The "Historical" origins are supported by the sources in section 7. The Ma'at, Isfet, and Weighing of the Heart origins come from general Egyptology; verify them before the docs are published.

## Sources

Section 7 of the scaffolding document, which the text above refers to, lists these sources:

- World History Encyclopedia, "Shabti Dolls: The Workforce in the Afterlife": https://www.worldhistory.org/article/119/shabti-dolls-the-workforce-in-the-afterlife/
- North Carolina Museum of Art, "Overseer Ushabti": https://learn.ncartmuseum.org/artwork/overseer-ushabti/
