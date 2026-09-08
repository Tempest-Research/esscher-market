# Research + execution discussion pack

**For Ben, Alex and Yaroslav** · 8 September 2026 · Draft 0.1

Two proposed, connected projects: a Qlib-based collaborative research laboratory and an execution pipeline that carries out selected portfolio targets in a simulator, later potentially a paper venue. The shared goal is reproducible research and an evidence trail explaining what happened during execution.

> **Discussion material, not a change to Esscher's approved plan.** Esscher hosts this pack so the group can read and critique it together. The two proposed projects remain separate; publication does not approve an integration, assign anyone work, or change this repository's permanently paper-only execution boundary. Esscher's [current plan](../plans/CURRENT.md) and [contribution rules](../../CONTRIBUTING.md) remain unchanged.

## Start here

- **Read the overview on GitHub:** [Project brief](01-project-brief.md).
- **Read or download the PDF:** [Research and Execution Group Brief](Research-and-Execution-Group-Brief.pdf). It is rendered from the project brief, including the same numbered sources.
- **For an agent:** use the [copy-ready research prompt](04-agent-research-brief.md#copy-ready-starting-prompt) and supply the six Markdown files below. Ask for independent critique and primary-source research, not automatic implementation. GitHub's **Raw** view supplies the plain Markdown for each file.
- **For technical discussion:** read [architecture and contracts](02-architecture-and-contracts.md) and challenge them against the actual pipeline.

## Markdown reading order

1. [README.md](README.md) — this index, scope and reading order.
2. [Project brief](01-project-brief.md) — standalone explanation of both projects, integration, Yaroslav's potential contribution, milestones, questions, glossary and sources. The canonical text for the PDF.
3. [Architecture and contracts](02-architecture-and-contracts.md) — proposed ownership, record semantics, timestamps, authority, retries, reconciliation and acceptance scenarios.
4. [Collaboration and roadmap](03-collaboration-and-roadmap.md) — contribution options, dependency-ordered milestones, evaluation and review expectations.
5. [Agent research brief](04-agent-research-brief.md) — copy-ready prompt, independent research tracks, evidence labels and expected review output.
6. [Sources and reading guide](05-sources-and-reading-guide.md) — public references, what each supports, limitations and focused reading questions.

## How to interpret the pack

This is an AI-assisted discussion draft prepared by Atlas at Ben's request. It is not an agreed team specification. Alex's code has not been inspected for this pack; Yaroslav's participation and responsibilities remain to be agreed. No current capability, profitable model, passing integration test or operational account connection should be inferred from proposed components.

The initial scope is historical research and local simulation. Broker-paper integration needs a separate decision; live-money trading is excluded. Source-backed upstream capabilities are not proof that the proposed integration exists.

No private repository content, local account identifiers, credentials or personal background are needed to use this pack. Share authorized repository/ref details with an agent separately only if appropriate. Never include secrets.

## Source preservation and revisions

The five numbered Markdown documents preserve the wording of the group discussion pack prepared on 8 September 2026. Line endings follow the repository's LF convention, and surplus trailing blank lines are removed. The PDF is byte-identical to the shared original. This README is adapted for GitHub navigation and repository-scope clarification.

For this draft, the project brief owns the overview and PDF wording; the architecture document owns detailed proposed interface semantics; the roadmap owns staged delivery; the agent brief owns research prompts; the reading guide owns the source map. Update the relevant Markdown source, reconcile any dependent summary, then regenerate the PDF. Do not edit a PDF as a competing source of truth.

**Next discussion:** inspect Alex's actual architecture, choose one compatible research/execution profile, and let each person choose a bounded contribution before starting implementation.
