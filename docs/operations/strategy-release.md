# Strategy release operations

`StrategyRelease` is a small, immutable PAPER boundary. It has a positive
release version, and canonical bytes carry one derived `release_sha256`;
callers do not author it. The record names the
packaged autonomy, strategy, V2 reasoner, latency, source matrix, risk,
lifecycle, and the three owner-lane bindings. Only build, evidence-report, and
security-report hashes are retained because their contents are outside the
build.

## Promote

Populate current semantic IDs, `PAPER` mode, valid Git/build/report hashes,
and qualified evidence/security flags. Run `evaluate_release(release)`, then
pass that exact result to `ReleaseLog.promote(release, decision)`. Evaluation
also requires the approved/evaluation-eligible V2 route and promotion-eligible
latency profile; synthetic latency remains rejected.

The first release has no predecessor. Each distinct successor names the exact
immediately preceding `supersedes_release_sha256`. Exact replay and reopen are
idempotent. There is no `latest`: callers use `load_exact(release_sha256)`,
which rejects superseded and revoked releases.

## Revoke and arm

`ReleaseLog.revoke()` appends a closed reason, operator ID and UTC time. Tables
have no-update/no-delete triggers. `ArmRecord` only binds a release, capability,
source, ledger, process, UTC start/expiry and flatten/recovery authorities for
#66. It does not register/claim arms, coordinate processes, or call a broker.

This boundary creates neither evidence nor a security report. #65 alone does
not make Esscher PAPER-ready.
