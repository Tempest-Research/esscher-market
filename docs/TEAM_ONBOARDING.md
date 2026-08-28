# Team onboarding

Read this before opening a Ringdown pull request. The purpose is not ceremony. It prevents three contributors and their automation tools from producing three incompatible systems.

## Current competition facts

The official event pages were verified on 28 August 2026. They stated that:

- submissions close on 4 September 2026 at 15:00 UTC;
- the final submission must use a new Alpaca paper account created for the hackathon with a USD 100,000 starting balance;
- the project must use Alpaca's Trading API through either its MCP server or CLI;
- the project must incorporate options;
- the paper account ID is required for judging;
- judging includes P&L, implementation, originality, and presentation.

The preserved page did not settle whether positions must be flat at submission, whether a minimum trade count exists, or whether AI assistance requires disclosure. Treat those as unconfirmed until an organizer publishes an answer. Paper performance is simulated, not evidence of live profitability.

Official event pages:

- <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon>
- <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon/live>

## Required reading for everyone

1. [README](../README.md): what exists today and what does not.
2. [Architecture](ARCHITECTURE.md): system boundaries and data flow.
3. [Source and claim policy](SOURCE_AND_CLAIM_POLICY.md): what counts as evidence.
4. [Contributing](../CONTRIBUTING.md): branch, test, review, and safety rules.
5. The relevant source files and tests for the lane being changed.

Before a first PR, each contributor must be able to explain, without reading generated prose:

1. Why information published after `decision_cutoff` is forbidden.
2. Why an abstention stays in the eligible-event denominator.
3. What residual return removes from the issuer's raw move.
4. Why a synthetic green test is not alpha or fill evidence.
5. Why Ringdown permits one frozen Alpaca adapter and no runtime REST fallback.

If an answer is unclear, research before coding. A plausible implementation built on the wrong market assumption is still wrong. It is merely wrong with tests.

## Lane-specific research

### Runtime and Alpaca integration

Read these official sources before changing execution code:

- [Alpaca MCP server documentation](https://docs.alpaca.markets/us/docs/alpaca-mcp-server)
- [Official MCP repository](https://github.com/alpacahq/alpaca-mcp-server)
- [Pinned audited MCP commit](https://github.com/alpacahq/alpaca-mcp-server/tree/872abbf28dab6cdde7d341fc13ac139b8002d1d9)
- [Paper trading](https://docs.alpaca.markets/us/docs/paper-trading)
- [Options trading](https://docs.alpaca.markets/us/docs/options-trading)
- [Options Level 3 and multi-leg orders](https://docs.alpaca.markets/us/docs/options-level-3-trading)

Prove exact tool names, argument shapes, response identity, and account mode from source or a sanitized capability receipt. Do not infer them from another team's code or an AI answer.

### Evidence, strategy, and evaluation

Research and record:

- whether each event is before market open, after market close, or intraday;
- the original publication time of every issuer or SEC source;
- the exact decision cutoff and feature snapshot time;
- the issuer, market, and sector prices measured over the same window;
- missing, revised, or contradictory evidence;
- the license or redistribution boundary for every dataset.

Start with four complete replay fixtures to validate contracts. Do not tune the policy from four cases. The Q-FAST panel requires at least 20 untouched point-in-time events.

### Static proof and submission

Read the frozen public trace schema before building UI. The public surface must be static and read-only. It must not contain credentials, account identifiers, databases, mutation SDKs, worker code, or outbound network capability.

Every scene must visibly distinguish:

- paper execution from offline evaluation;
- `SYNTHETIC_CONTRACT_FIXTURE` from historical data;
- `INDICATIVE_DATA` from executable option prices;
- engineering evidence from alpha or profitability evidence.

Do not invent a broker receipt to complete a screen. Show an explicit unavailable or simulated state instead.

## Rules for automation-assisted work

Automation may help inspect, draft, test, or review. The human contributor still owns every line and claim.

- Never put credentials, private account IDs, private datasets, or unpublished team material into a model prompt.
- Read the real file and relevant tests before proposing a change.
- Ground external API behavior in an official URL or pinned source revision.
- Treat generated code and summaries as untrusted until the diff and execution output are checked.
- Never fabricate commands, test output, datasets, citations, fills, or receipts.
- Never copy code, assets, copywriting, or fixtures from competing submissions.
- Do not add dependencies unless the PR explains why the standard library or existing stack is insufficient.
- Do not perform broad refactors while delivering a lane task.
- Remove local agent instruction files and generated work logs from the commit.
- Before review, explain the change's purpose, data flow, invariant, failure mode, and trade-off.

## Ownership

- Runtime/contracts and Alpaca integration: Ben, with one implementation owner per file.
- Evidence/strategy/evaluation: `MS-Mesh`.
- Static proof/demo/submission: `akurkar07` after the pending repository invitation is accepted.
- Final integration and public wording: Ben.

GitHub ownership is encoded in [CODEOWNERS](../.github/CODEOWNERS). Cross-lane changes are proposed through a small contract PR; two people do not edit the same file concurrently.
