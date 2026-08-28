# Contributing

Ringdown is currently an offline research harness. Keep each change inside that boundary unless the team explicitly accepts a new execution contract.

## Workflow

1. Branch from `main`.
2. Add or change a failing test first for behavior changes.
3. Run:

   ```bash
   ruff check .
   ruff format --check .
   pytest
   ```

4. Push the branch and open a draft pull request.
5. Do not request reviewers until the PR wording and diff are ready.
6. Merge only after CI passes and one teammate reviews the change.

## Hard boundaries

- Never commit credentials, account identifiers, private event data, or `.env` files.
- No live-money trading.
- No broker mutation path without an explicitly accepted adapter and execution-mode contract.
- Synthetic fixtures must say `SYNTHETIC_CONTRACT_FIXTURE` and `NOT_ALPHA_EVIDENCE`.
- Historical panels must be point-in-time, retain abstentions, and use at least 20 events for Q-FAST.
- A green contract fixture proves software behavior, not profitability.
