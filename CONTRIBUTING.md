# Contributing

Ringdown is a paper-only research system with strict evidence and broker boundaries. A contribution is complete when another teammate can reproduce it and explain why it is safe.

Read [team onboarding](docs/TEAM_ONBOARDING.md), [architecture](docs/ARCHITECTURE.md), and the [source and claim policy](docs/SOURCE_AND_CLAIM_POLICY.md) before a first pull request.

## Setup

```bash
uv sync --extra dev
uv run python scripts/check_repo_hygiene.py
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Use `uv run`; do not depend on a globally installed Python package.

## Branch and ownership workflow

1. Start from current `main`.
2. Use `feat/<short-name>`, `fix/<short-name>`, `data/<short-name>`, or `docs/<short-name>`.
3. Change only files owned by your lane. See [.github/CODEOWNERS](.github/CODEOWNERS).
4. Propose shared-contract changes before editing another lane's files.
5. Write a failing regression before behavior code where practical.
6. Keep one concern per pull request. Do not mix formatting, refactors, and features.
7. Open a draft pull request with no reviewers requested.
8. Mark it ready only after the diff, public wording, and verification receipts are complete.
9. Merge only after CI passes, the current head has one approval, and conversations are resolved.

Never push directly to `main`. Do not force-push a branch another contributor is using.

## Automation-assisted changes

Automation output is an untrusted proposal, not evidence.

- The human contributor owns the diff and must understand it.
- Never place credentials, account identifiers, or private datasets in prompts.
- Verify external contracts against official documentation or pinned source.
- Read the actual changed files and tests before requesting review.
- Do not include generated work logs, local agent instructions, or automated co-author trailers.
- Do not fabricate commands, output, citations, data, fills, or receipts.
- Do not copy code, assets, or language from competing submissions.

Before review, be ready to explain the purpose, one data-flow trace, one invariant, one failure mode, and the chosen trade-off without relying on generated prose.

## Safety boundaries

- Execution is permanently limited to Alpaca paper accounts. Do not add another account mode.
- Paper-account mutation requires an accepted execution contract and exactly one official MCP adapter.
- Do not add direct REST, CLI, or second-adapter fallbacks.
- No blind retry after an ambiguous order submission.
- Never commit credentials, account identifiers, private event data, `.env` files, or local tool instructions.
- Synthetic fixtures require `SYNTHETIC_CONTRACT_FIXTURE`, `NOT_HISTORICAL_DATA`, `NOT_ALPHA_EVIDENCE`, and `NO_BROKER_EXECUTION`.
- Historical panels must be point-in-time, retain abstentions, and use at least 20 eligible events for Q-FAST.
- Green tests prove software behavior, not alpha, fill quality, or profitability.
- Public artifacts remain static, sanitized, and read-only.

## Pull-request evidence

Every pull request states:

- lane and owned paths;
- exact behavior changed and non-goals;
- official sources for external API or market assumptions;
- commands run and observed results;
- data class and claim boundary;
- safety impact and rollback;
- version impact: `major`, `minor`, `patch`, or `none`.

Use the repository pull-request template. A pasted green checkmark without the command and result is not a receipt.
