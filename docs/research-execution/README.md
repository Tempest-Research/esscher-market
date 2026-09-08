# Research + execution discussion pack

**For Ben, Alex and Yaroslav** · 8 September 2026 · Draft 0.2

**First: understand one experiment and make it useful to a teammate. Later: connect independently useful research and execution projects.** Qlib is the first learning and fit-test candidate, not a final foundation decision. The initial product hypothesis is a shared experiment workbench that helps a small code-based research team reproduce, compare, challenge and explain results.

> **Discussion material, not a change to Esscher's approved plan.** Esscher hosts this pack so the group can read and critique it together. The two proposed projects remain separate; publication does not approve an integration, assign anyone work, or change this repository's permanently paper-only execution boundary. Esscher's [current plan](../plans/CURRENT.md) and [contribution rules](../../CONTRIBUTING.md) remain unchanged.

## Start here

- **What to work from now:** [Collaboration and roadmap](03-collaboration-and-roadmap.md) — the learning path, research-only milestones and exact resume point.
- **Why this project:** [Project brief](01-project-brief.md) — intended users, first journey, reuse boundary and later integration.
- **Next learning exercise:** prediction scores -> portfolio-selection rule -> small checked return/cost calculation -> interpreted backtest. Keep programming and maths foundations primary; do not generate a full platform as a substitute for understanding it.
- **For an agent:** use the [copy-ready research prompt](04-agent-research-brief.md#copy-ready-starting-prompt) with all six Markdown files. Ask for critique or a bounded fit test, not automatic implementation or another broad discovery campaign.
- **For later integration discussion:** [Architecture and contracts](02-architecture-and-contracts.md) — provisional requirements to test against actual inputs/outputs, not a frozen API.

## What changed in Draft 0.2

- One first journey: an author compares a baseline/candidate, then a teammate reproduces, varies and explains the result without verbal help.
- L0 connects guided Qlib understanding to small programming/maths exercises; it is not a publication exam or an assessed claim about anyone's ability.
- M1 is research-only and no longer depends on M0's execution inspection or a compatible adapter.
- M1U makes teammate usefulness an explicit milestone. Record friction with existing tools before deciding whether custom work is needed.
- M0 validates one real research-output/simulator-input mapping before detailed contracts are ratified.
- M2-M4 remain later integration work; the first discrepancy study is a controlled fee comparison with fixed fills and explicit limits.

The changes incorporate Ben's requested plan review and follow-up discussion. They do not establish product demand, complete any milestone, select a platform, or promise profitable research.

## Markdown reading order

1. [README.md](README.md) — current index, changes and scope.
2. [Project brief](01-project-brief.md) — user journey, learning approach, both projects and integration boundaries.
3. [Architecture and contracts](02-architecture-and-contracts.md) — proposed ownership, semantics, authority, recovery and the later fee investigation.
4. [Collaboration and roadmap](03-collaboration-and-roadmap.md) — authoritative milestone dependencies, acceptance, contribution options and resume point.
5. [Agent research brief](04-agent-research-brief.md) — bounded review tracks, evidence labels and copy-ready prompt.
6. [Sources and reading guide](05-sources-and-reading-guide.md) — public references, limitations and purpose-led reading order.

## How to interpret the pack

This is an AI-assisted discussion draft prepared by Atlas at Ben's request, not an agreed team specification. Alex's code has not been inspected for this pack; Yaroslav's participation and responsibilities remain to be agreed. No current capability, profitable model, passing integration test or operational account connection should be inferred from proposed components.

Start with historical research and local simulation. Broker-paper integration needs a separate decision; live-money trading is excluded. Source-backed upstream capabilities, guided notebook runs and documentary reviews are not proof of a useful product, valid investment evidence or a completed integration.

No private repository content, local account identifiers, credentials or personal background are needed to use this pack. Share authorized repository/ref details separately only if appropriate. Never include secrets.

## Revision ownership and original PDF

The six Markdown documents are the current Draft 0.2 working pack. The project brief owns the overview; architecture owns proposed interface/analysis semantics; the roadmap owns dependencies and acceptance; the agent brief owns prompts; the reading guide owns the source map. Update the owning source and reconcile its summaries together. Text uses UTF-8, LF endings and a final newline.

The [original Draft 0.1 PDF](Research-and-Execution-Group-Brief.pdf) is retained unchanged as a **historical snapshot, not the current plan**. Its original source is [the project brief at the published Draft 0.1 commit](https://github.com/Tempest-Research/esscher-market/blob/0ed306c59388bbea6540b27d9bfce029eddb4396/docs/research-execution/01-project-brief.md). It does not include the learning-first or M1U changes. Use the Markdown above for current work; any future current-edition PDF must be regenerated from the revised source and clearly versioned rather than editing this archive as a competing source.

**Next step:** continue the small research learning example. Discuss execution compatibility separately when actual samples are available; it does not block research learning or the first teammate-usefulness test.
