## Summary

What changed and why?

## Verification

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `pytest`

## Safety boundary

- Data class: `SYNTHETIC_CONTRACT_FIXTURE` / `POINT_IN_TIME_EVENT_PANEL` / N/A
- [ ] No credentials or private data
- [ ] No broker mutation path
- [ ] Claims remain `PAPER`, `INDICATIVE_DATA`, or `NOT_ALPHA_EVIDENCE` where applicable

## Version impact

`major` / `minor` / `patch` / `none`
