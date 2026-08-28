## Change

What changed, why, and what is deliberately out of scope?

## Ownership

- Lane:
- Owned paths changed:
- Cross-lane contract approved: N/A / link
- Version impact: `major` / `minor` / `patch` / `none`

## Source grounding

List the official documentation, pinned source revision, or event manifest used for every external contract or market assumption.

- Source:
- Data class: `SYNTHETIC_CONTRACT_FIXTURE` / `POINT_IN_TIME_EVENT_PANEL` / `INDICATIVE_DATA` / N/A

## Verification

- [ ] `uv run python scripts/check_repo_hygiene.py`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run pytest`
- [ ] `uv build`
- [ ] Built wheel installed outside the checkout; version and primary CLI smoke passed

Paste the exact command and observed summary:

```text
command:
result:
```

## Safety and claims

- [ ] No credentials, account identifiers, private data, or local automation files
- [ ] No unapproved broker mutation or direct-REST fallback
- [ ] Ambiguous external state fails closed or enters reconciliation
- [ ] `PAPER`, `INDICATIVE_DATA`, and evidence limitations remain visible where applicable
- [ ] No alpha, profitability, executable-fill, or production-readiness claim exceeds the evidence
- [ ] Public artifacts are static and sanitized

## Comprehension

Explain briefly in your own words:

- Purpose and behavior change:
- One input-to-output data flow:
- Safety invariant preserved:
- Failure or edge case covered:
- Trade-off chosen:

## Rollback

How can this change be disabled or reverted without corrupting evidence or external state?
