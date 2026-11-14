# State machine, evidence ladder and observation window

## 1. Four evidence layers

| Layer | Observable evidence | Locally decidable | Written to |
|---|---|---|---|
| L1 Action | Form POST 2xx + thank-you URL + screenshot; mail `accepted` + `provider_id` | yes | `submitted_at`, `confirmation_ref`, evidence |
| L2 Transport | `GET /emails/{id}` -> `delivered` / `bounced` / `complained` | yes | `outreach-receipts.json` |
| L3 Acceptance | Whether the other side received and reviewed it | **no** | never; a `gaps` entry instead |
| L4 Live link | An `<a>` pointing at `own_url` in `target_url` HTML | yes, long window | `live_at`, `verify` snapshot |

`delivered` proves an MX accepted a message, not that a person read it. The word
"guaranteed" is not permitted (R-530).

## 2. link-ledger field mapping

| Purpose | Field |
|---|---|
| Landing page | `target_url` (third-party page where the link appears) |
| Our page | `own_url` |
| Form address | `submission_url` (submit page or form action, distinct from `target_url`) |
| State | `state`: `registered / submitted / approved / live / indexed / lost / rejected / error` |
| Hops | `created_at`, `prepared_at`, `registered_at`, `submitted_at`, `approved_at`, `live_at`, `indexed_at`, `lost_at` |
| Receipt | `confirmation_ref` (a real receipt path or id; never fabricated, R-414) |
| Latest scalars | `http_status`, `link_present`, `rel`, `anchor_text`, `last_checked_at`, `next_check_due` |
| Verification | `verify{outcome, content_hash, http_status, observed_href, host_linked, host_href, rel, fetched_at, tool}` |
| Baseline | `confirmed_rel` (written once at the moment `live` is confirmed) |
| Fallback | `notes`, `gaps[]`, `scores.gaps` |

The row contract is closed (`additionalProperties: false`), and `verify` is closed
too. Extra keys make validation fail. `provider_id`, batch ids and observation
conclusions go into `notes`; missing values go into `gaps` as
`field + reason + fallback`, never as an estimate.

## 3. Two state layers, one direction

```
backlink-prospector snapshot (read-only candidates)
      -> enrichment + human confirmation
OUTREACH-QUEUE.status  pending / in_progress / submitted / failed / skipped / verified
      -> human confirmation + contract validation
link-ledger.state      registered -> submitted -> approved -> live -> indexed -> lost
```

`verified` is refused by `record_result.py`. Whether a link exists is a measurement,
not a submission result; an executor may write `status` but never `state`.

## 4. Observation window

The terminal criterion is L4. Two consequences:

1. A short window cannot decide success. Editorial review takes days to weeks; one
   directory states 30-60 days for its free tier.
2. R-512 and R-513 give `submitted` rows **no fetch budget**: only `live` / `indexed`
   rows enter the biweekly check set. Without a new rule, L4 cannot be reached.

**Adopted approach (A, zero contract change).** Add a pending-effect observation
window rule: fetch `target_url` at T+1/3/7/14/30 days, at most 6 times
(`LINK_RECHECK_LIMIT`), with a UA and a delay; record observations in the derived
`outreach-receipts.json`; reuse `next_check_due` and `last_checked_at`; a human runs
`linkloop.confirm` to promote a found link to `live`.

Rejected approach (B): adding `observing` to the `state` enum requires a contracts
minor bump across the version matrix, twelve lock files, every SKILL frontmatter,
changelogs and docs. Frozen schemas should not be created for shapes that are not
settled.

Implemented locally: `bdl/services/window.py` provides `plan`, `next_check_due`,
`classify` and `terminal_state`. It does not fetch pages; fetching belongs to a
future rule-backed worker.

## 5. Time windows

| Window | Action | Permitted conclusion |
|---|---|---|
| T+0 to 2 min | Record the transport receipt | `submitted` / `failed` |
| T+5 min to 24 h | Poll provider events with backoff | `delivered` / `bounced` / `complained` |
| T+1/3/7/14/30 d | Read-only fetch of `target_url` | `found` / `absent` / `unknown` |
| Window end | Long-term recheck, write `next_check_due` | keep `submitted`; never fail, never re-pitch |

Discipline:

- 403, timeout, robots refusal and JS-only injection are `unknown`, not `absent`.
- A single `absent` never becomes `lost` (R-513).
- `approved` is not `live`; `live` requires a fetched page (R-512).
- Compare URLs by normalised host + path; `observed_href` may be relative or carry
  tracking parameters.
- Pin `verify.content_hash` only when `found`, to distinguish deletion from a
  page rebuild.
