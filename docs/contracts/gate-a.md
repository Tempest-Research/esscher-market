# Gate A competition and account contract

Issue #40 owns changing organizer, account, entitlement, and broker-capability facts. It does not
choose a strategy, risk budget, expression, or exit. The contract is permanently PAPER-only and
contains no credential or raw account identifier.

## Current result

The official event page was retrieved on 30 August 2026 at 20:54:04 UTC. The canonical exact-excerpt
snapshot is packaged as `esscher/contracts/policies/gate_a_programme_v1.json` with SHA-256
`40c2e780c684bdde671b028dbdd8c9b13268e659c24e98a2d452ff7c8692f955`.

The current result is `ENTRY_DISABLED`. Four entry-relevant organizer rules remain unpublished:

- exact P&L mark and judging snapshot;
- official cost treatment;
- leverage or margin limits; and
- drawdown and forced-flatten rules.

Account-specific facts are delegated to a separate short-lived capability receipt. The committed
fixture deliberately records them as `INACCESSIBLE`; it is a schema and fail-closed test, not an
account observation. A future verified receipt cannot override an unpublished organizer rule.

## Organizer facts

Each fact has one stable ID, status, value or explicit limitation, official source URL, retrieval
time through its parent snapshot, exact quote when verified, and an `affects_entry` flag.

Allowed statuses are:

- `VERIFIED`: exact official quote and normalized value are both present;
- `CONTRADICTORY`: evidence conflicts and no value is selected;
- `INACCESSIBLE`: the source does not publish or expose the required value;
- `NOT_APPLICABLE`: the field does not apply, with a recorded reason.

The official event page currently verifies:

- submission deadline: 4 September 2026 at 16:00 BST, normalized to 15:00 UTC;
- a fresh dedicated Alpaca PAPER account for judging;
- competition starting balance of USD 100,000;
- Alpaca Trading API plus MCP or CLI;
- options incorporated in every strategy;
- P&L, technology, creativity, and presentation judging criteria;
- one-page AI logic, risk gate, and Alpaca infrastructure write-up;
- account ID supplied privately for judging; and
- original, MIT-compliant submissions.

The page publishes no judging weights, exact P&L mark, cost convention, leverage limit, drawdown
rule, flatten rule, or account-specific data/capability state. Those values are not inferred.

## Capability receipt

`esscher.gate_a_capability_receipt/v1` binds the exact programme-contract hash, pinned Alpaca MCP
`2.3.0` commit `872abbf28dab6cdde7d341fc13ac139b8002d1d9`, producer build, observation and
expiry times, a one-way account fingerprint, and these required observations:

- PAPER endpoint class;
- account status;
- fresh dedicated-account state;
- USD 100,000 starting balance;
- account reset/activity state;
- equity and option feed identities;
- option trading level;
- PAPER multi-leg support; and
- the exact required MCP tool surface.

Every verified observation carries a hash of its private source evidence. Evaluation counts a
verified observation only when the receipt producer build exactly matches an externally approved
build identity and the caller supplies immutable evidence bytes matching that hash. The trust anchor
is explicit input; parsing a self-asserted receipt never authorizes entry.

Raw account IDs, account numbers, credentials, secret-like text, and raw broker responses are
forbidden from the receipt. An unauthorized producer, absent or mismatched evidence, future or
expired receipt, wrong programme hash, missing fact, unsupported value, or non-`VERIFIED`
observation keeps entry disabled.

## Official sources

- Hackathon event and rules: <https://lablab.ai/ai-hackathons/alpaca-ai-trading-agents-hackathon>
- Alpaca PAPER behavior and limitations: <https://docs.alpaca.markets/docs/paper-trading>
- Alpaca market-data plans and feeds: <https://docs.alpaca.markets/us/docs/about-market-data-api>
- Alpaca Level 3 and multi-leg behavior: <https://docs.alpaca.markets/us/docs/options-level-3-trading>
- Alpaca MCP configuration and toolsets: <https://docs.alpaca.markets/us/docs/alpaca-mcp-server>

Official product documentation establishes possible behavior, not the state of one account. Only a
current sanitized runtime receipt may verify account-specific values.

## Reproduction

```bash
uv run --extra dev pytest tests/test_gate_a_contract.py -q
uv run --extra dev python scripts/check_repo_hygiene.py
```

Tests use canonical local fixtures and make no network, account, MCP, or broker call.