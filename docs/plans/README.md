# Esscher plans and research archive

This directory gives the team one place to read the current plan, understand how it changed, and inspect the evidence and competing proposals behind it.

## Reading order

1. Read [`CURRENT.md`](CURRENT.md). It is the only current team-level decision boundary in this directory.
2. Read [`archive/2026-08-30-strategy-first-issue-plan.md`](archive/2026-08-30-strategy-first-issue-plan.md) to understand the live GitHub issue graph and its original assumptions.
3. Use the independent research and review documents to challenge the current plan. Do not implement directly from them.
4. Use dated drafts only to understand prior reasoning and changes.

## Status meanings

- **Current:** integrated team plan. Policy conflicts must be resolved here before implementation.
- **Live-tracker snapshot:** record of active GitHub planning at the capture time; it may lag the integrated plan.
- **Superseded draft:** useful history, not implementation authority.
- **Research input:** independent evidence or strategy proposal awaiting explicit reconciliation.
- **Architecture review:** independent critique, not an approved design by itself.

## Document index

| Document | Status | Purpose | SHA-256 |
|---|---|---|---|
| [`CURRENT.md`](CURRENT.md) | **Current** | Complete end-state architecture, LLM responsibility, open strategy decisions, build dependencies, and proof requirements | `972e70034a07cf64b495440187a1268fe47a9af070a8f487ece9e8045a24effa` |
| [`archive/2026-08-30-strategy-first-issue-plan.md`](archive/2026-08-30-strategy-first-issue-plan.md) | Live-tracker snapshot | Readable snapshot of issues #3, #9, and #26–#33, including dependencies and assumptions | `df029cb7f97e7b210e2b163a6a8562d85cff3fb75e5815ae1b70ecd48d964536` |
| [`archive/2026-08-30-autonomous-llm-plan-draft.md`](archive/2026-08-30-autonomous-llm-plan-draft.md) | Superseded draft | Earlier autonomous LLM research-to-PAPER plan; preserved as historical content, including obsolete reduced-scope terminology | `9f580263a3788d2de01114eb0306c9ea88f23d1e3766ff3d2fc106f4e40eb345` |
| [`research/2026-08-30-quant-data-ai-evidence-memo.md`](research/2026-08-30-quant-data-ai-evidence-memo.md) | Research input | Point-in-time data, universe, feature, AI, falsification, and sample-size analysis | `b978700605b651d30e45ed8a0da0403fe29aa114b082451ef8cf6e52538bc246` |
| [`reviews/2026-08-30-research-to-paper-architecture-challenge.md`](reviews/2026-08-30-research-to-paper-architecture-challenge.md) | Architecture review | Source-grounded challenge of decision/package authority, option evidence, contracts, and work ordering | `c6356d50472e45769da64c33b8d91ea06723d0597fe1cdb60ac5fdd7cf81c8c5` |
| [`research/2026-08-30-clean-room-quant-firm-cto-report.md`](research/2026-08-30-clean-room-quant-firm-cto-report.md) | Research input | Blank-slate small-firm CTO proposal focused on competitive PnL, implementation, candidate selection, equities/options, execution truth, and judge proof | `dc33cdc3a740ae84b539d2e39354db83405d814e881bd68af72888b8cfdf3e2b` |
| [`research/2026-08-30-clean-room-quant-researcher-report.md`](research/2026-08-30-clean-room-quant-researcher-report.md) | Research input | Blank-slate chief-quant proposal for systematic post-earnings underreaction in liquid equities, with conditional options convexity | `7b9228ce8b064376f1ae7eb47bbccf39ecdfecb0a37d1c8548104f3e1e3cfccb` |
| [`research/2026-08-30-clean-room-tournament-portfolio-report.md`](research/2026-08-30-clean-room-tournament-portfolio-report.md) | Research input | Adversarial tournament portfolio analysis covering raw-upside, high-finish-probability, and judge-defensible playbooks | `c76a32aec8342ac2773a00f3d4158fccab30638858b9d57f60be76576eb3e51b` |
| [`research/2026-08-30-external-chatgpt-aegis-macro-desk-plan.md`](research/2026-08-30-external-chatgpt-aegis-macro-desk-plan.md) | Research input | Ben-supplied online ChatGPT deep-research brief proposing scheduled macro interpretation, SPY confirmation, deterministic debit spreads, and a Trade Passport | `d24cb46164b4c5642140b0312e6f679d46bbc3d038965861638e708dedf104b7` |
| [`reviews/2026-08-30-independent-quant-firm-synthesis.md`](reviews/2026-08-30-independent-quant-firm-synthesis.md) | Research synthesis | Reconciles the four blank-slate reports, audits the Aegis source claims, and proposes earnings as the primary research candidate, macro as a challenger, and Aegis controls as shared architecture | `bfb5f26ddbea369982e591a5d9c4f6898996952c5b07b872df74d468d42205b1` |

The source reports are content-preserving repository copies. UTF-8 line endings, trailing Markdown hard-break whitespace, and fenced Python layout may be normalized to satisfy repository hygiene and the repository formatter. The recorded hashes identify the canonical Git blobs, allowing the team to detect accidental edits and compare later revisions without pretending two different reports are the same artifact.

## Pending additions

No requested report is represented by an empty placeholder. Ben's additional external research, if supplied separately from the Aegis brief, should be added only after the complete artifact and cited sources are available.

Reconcile accepted findings into `CURRENT.md` separately; copying a report or synthesis into this directory does not approve it.

## Plan-authority rules

- `CURRENT.md` owns integrated architecture and unresolved decisions.
- GitHub issues own execution assignments after their policy assumptions agree with `CURRENT.md`.
- `research/` and `reviews/` supply evidence and challenge; they do not authorize code, data purchases, broker actions, or policy changes.
- `archive/` preserves history. Never silently rewrite a dated source document to make it look current.
- Current user authority, competition rules, data entitlements, and actual broker state outrank every document here.
- Alpaca PAPER remains the only execution environment.

## Adding a plan or report

1. Use a dated, descriptive filename.
2. State its status, source, evidence basis, and repository snapshot where relevant.
3. Distinguish facts, assumptions, recommendations, and unresolved questions.
4. Preserve the original source artifact where possible.
5. Update this index and record its SHA-256.
6. Reconcile accepted decisions into `CURRENT.md`; do not make readers infer the winner from several conflicting documents.
