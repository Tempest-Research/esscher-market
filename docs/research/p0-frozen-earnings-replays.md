# P0 frozen earnings replay evidence

Issue #2 freezes four future scheduled-earnings events before any post-cutoff source path or outcome value is added. The selected events cover three sectors and both pre-open and after-close timing buckets.

## Frozen artifacts

- `data/earnings-replays/selection-rule-v1.json` owns the inclusion rule and claim boundary.
- `data/earnings-replays/event-list-v1.json` owns the ordered event identities and event context.
- `data/earnings-replays/events/*.json` contains one strict `ringdown.point_in_time_evidence_manifest` v2 document per event.
- Every event manifest binds the exact selection-rule and event-list bytes by SHA-256.
- The first event-list commit is `af9ec9363adf81b0e416102df140af74c8ce61c4`; no post-cutoff path exists in that commit or in this change.

Sector and market/sector proxy values are frozen protocol classifications used to enforce panel diversity and define later residual-return inputs. They are not attributed to issuer schedule pages; `field_source_refs` is reserved for facts those source records actually support.

Kroger's issuer announcement fixes its September 11 call at 8:00 a.m. ET, corroborated by the official dissemination record.[1][2] General Mills fixes its September 23 results Q&A at 8:00 a.m. CT.[3] Micron fixes its September 30 call at 2:30 p.m. Mountain time.[4] NIKE fixes its October 1 release at approximately 1:15 p.m. PT after the regular session close.[5] Regular-session windows are grounded in the official exchange calendar and hours page.[6]

## Manifest v2 boundary

Schema v2 is data-only. `validate_replay_evidence_set` accepts it only as frozen replay evidence and returns `permit_eligible=False`. The permit compiler continues to accept only evidence-manifest v1, so a v2 document cannot authorize broker mutation.

The validator rejects:

- missing or unknown fields;
- duplicate event IDs or duplicate JSON fields;
- event-list or selection-rule hash mismatches;
- context that differs from the frozen list;
- missing source references or unresolved feature dependencies;
- source publication bounds, retrieval timestamps, snapshots, or freezes after the decision cutoff;
- missing claim-boundary qualifiers;
- any freeze-stage event list containing post-cutoff paths or outcome fields.

A source without a source-supported time-of-day uses a conservative UTC publication interval instead of an invented instant. Raw source pages are not redistributed; committed manifests retain URLs, publisher identity, retrieval time, content hash, entitlement notes, and limitations.

## Reproduction

```bash
uv sync --extra dev
uv run pytest tests/test_replay_evidence.py -q
uv run pytest tests/test_research_to_permit.py -q
```

No event manifest contains an earnings result, return, option quote, fill, P&L value, or broker action. These artifacts are engineering evidence only, not alpha or profitability evidence.

## Sources

[1] https://ir.kroger.com/news/news-details/2026/Kroger-Announces-Second-Quarter-Conference-Call-with-Investors — Kroger Announces Second Quarter Conference Call with Investors
    > "The Kroger Co. (NYSE: KR) announced today it will host its second quarter 2026 earnings conference call at 8:00 a.m. ET on Friday, September 11, 2026."
[2] https://www.prnewswire.com/news-releases/kroger-announces-second-quarter-conference-call-with-investors-302852069.html — Kroger Announces Second Quarter Conference Call with Investors
    > "The Kroger Co. (NYSE: KR) announced today it will host its second quarter 2026 earnings conference call at 8:00 a.m. ET on Friday, September 11, 2026."
[3] https://investors.generalmills.com/press-releases/press-release-details/2026/General-Mills-to-Webcast-Fiscal-2027-First-Quarter-Earnings-Results-on-September-23-2026 — General Mills to Webcast Fiscal 2027 First Quarter Earnings Results on September 23, 2026
    > "A press release, pre-recorded management remarks and supporting slides will be issued that morning followed by a webcasted question and answer session on the results at 8 a.m. CT."
[4] https://investors.micron.com/news/press-release/2026/Micron-Technology-to-Report-Fiscal-Fourth-Quarter-Results-on-September-30-2026 — Micron Technology to Report Fiscal Fourth Quarter Results on September 30, 2026
    > "Micron Technology, Inc. (Nasdaq: MU) announced today that it will hold its fiscal fourth quarter earnings conference call on Wednesday, Sep. 30, 2026, at 2:30 p.m. Mountain time."
[5] https://investors.nike.com/investors/news-events-and-reports/investor-news/investor-news-details/2026/NIKE-Inc--Announces-First-Quarter-Fiscal-2027-Earnings-and-Conference-Call — NIKE first quarter fiscal 2027 earnings schedule
    > "NIKE, Inc. (NYSE: NKE) plans to release its first quarter fiscal 2027 financial results on Thursday, October 1, 2026, at approximately 1:15 p.m. PT, following the close of regular stock market trading hours."
[6] https://www.nyse.com/trade/hours-calendars — NYSE Holidays and Trading Hours
    > "All NYSE markets observe U.S. holidays as listed below for 2026, 2027, and 2028."
    > "Core Trading Session: 9:30 a.m. to 4:00 p.m. ET"
