---
name: backlink-delivery-loop
description: This skill should be used when preparing, running or reviewing a backlink delivery loop for a center domain - importing versioned prospect snapshots from tokencoup-skills-backlink-prospector (store JSON or CSV export), structuring contact addresses from operator prose, computing cadence and the submitted-to-live observation window, writing a review-only delivery manifest, recording the delivery actions a human performed, polling the sending provider for delivered / bounced transport receipts, and projecting the event log into a current state per domain and channel. It never sends mail and never submits a form.
agent_created: true
---

# Backlink delivery loop

## Purpose

Turn a center domain's prospect pool into a **reviewable delivery manifest** with a
complete audit trail, then observe whether a link actually goes live. The skill is a
standalone distribution package: it is not auto-loaded by the WorkBuddy skill
scanner and must be installed explicitly.

**Hard boundary.** This package never transmits: no form submission, no mail send.
Every network call it makes is a read-only GET (the L4 page observer and the L2
provider receipt poll), and `BDL_AUTO_TRANSMIT=true` is rejected at startup.
Transmission stays behind a human review gate (R-520, R-530 tier B on class U
targets).

## Invariants

| Term | Definition |
|---|---|
| `center_domain` | Our own center domain, e.g. `soundbuttons.cc`. Resolved from the site slug, never written into a closed ledger row. |
| `target_domain` | The third-party domain that may host the link. |
| Primary key | `(center_domain, target_domain, channel)` where `channel` is `form` or `email`. A domain can be both; they are tracked, cooled down and counted separately. |
| Delivery channel | A property of the landing page, not of the domain. `form` requires a submission URL, `email` requires a mailbox. |
| Source of truth | `sites/<slug>/07-links/link-ledger.json` with the `link-ledger` contract |
| Terminal success | `state: live` with `verify.outcome: found` (L4). There is no "delivery succeeded" state. |

The upstream snapshot `url` is a **site entry point**, not `link-ledger.target_url`.
Never promote it to a landing page: doing so makes every L4 fetch look at the wrong
page and produces systematic false negatives.

## Configuration

All behaviour is driven by environment variables, validated once at startup:

| Variable | Default | Meaning |
|---|---|---|
| `BDL_HOME` | `.` | Package root. |
| `BDL_SNAPSHOT_DIR` | `./data/snapshots` | Where exported snapshots live. |
| `BDL_PROSE_DIR` | `./assets/prose` | Operator prose for contact extraction. |
| `BDL_OUT_DIR` | `./data/out` | Where manifests and receipts are written. |
| `BDL_HISTORY_DIR` | `./data/history` | Directory of additional history files (JSON/JSONL) for reconciliation. |
| `BDL_TEMPLATES_DIR` | `./data/templates` | Per-centre template overrides (see below). |
| `BDL_OBSERVATION_DIR` | `./data/observations` | Where scan ledgers and confirm proposals are written. |
| `BDL_RECORDS_DIR` | `./data/records` | Append-only delivery event log, one `events-<center>.jsonl` per centre. |
| `BDL_FORMS_DIR` | `./data/forms` | Form fingerprint registry (`registry.json`). |
| `BDL_CENTER_DOMAIN` | - | Our center domain (required for most commands). |
| `BDL_OWN_SITE_BASE` | derived | Base URL of the center site. |
| `BDL_TIER` | `B` | `A` or `B`; `A` requires a recorded human authoriser. |
| `BDL_AUTO_TRANSMIT` | `false` | `true` is rejected: transmission stays behind the human gate. |
| `BDL_AUTO_CONFIRM` | `false` | When `true`, a found L4 link is marked `live` in the local ledger and queued as a confirm proposal. The shared link-ledger is never written here. |
| `BDL_DEFERRAL_WINDOW_DAYS` | `90` | Repeat-pitch deferral window per target domain (R-522 cadence). |
| `BDL_DAILY_CEILING` | `100` | Per-target-domain daily ceiling (R-530 instance override). Ceiling, not a starting rate: warm the sending domain up gradually. |
| `BDL_RECEIPT_WINDOW_DAYS` | `30` | How long a send or submission may stay unanswered before it is closed as unanswered. |
| `BDL_CONTACT_LOST_DAYS` | `180` | Cooldown for an address that bounced. Attached to the address, not the domain. |
| `BDL_PROVIDER` | - | Sending provider whose receipt API is read: `resend` or `postmark`. Unset means receipts are reconciled by hand. |
| `BDL_PROVIDER_BASE_URL` | per provider | Override the receipt API base (a local mock, a regional endpoint). |
| `BDL_PROVIDER_API_KEY` | - | Read-only key for the receipt API. Never enters the config object, never printed. Absent means no poll is possible. |
| `BDL_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR`. |

### Templates (configurable per channel type)

`templates.py` ships built-in copy for every `channel_type` (asset, guest_post,
directory, community, journalist, other), each with an email (`subject` + `body`)
and a form (`fields` map). Override any channel by dropping a JSON file into
`BDL_TEMPLATES_DIR`: `templates.json` overrides every centre, and
`<center_domain>.json` overrides only that centre. Files are merged into the
built-ins - supply only what you want to change. Rendering rejects any placeholder
not in context and any CJK in email copy (R-522).

## Workflow

1. `doctor` - validate configuration, confirm the transmission gate is closed.
2. Produce a snapshot from the prospector, one of two ways:
   - `export-snapshot --store <prospector store-<domain>.json> --out <snapshot.json>
     [--enrichment <data/enrichment-<center>.json>]` - reads the internal store. It
     carries no `is_gap` column, so that field is derived ("not acquired and not a
     competitor") and the derivation is reported. `--enrichment` folds in the
     read-only discovery overlay (contract C 1.1.0): `submission_url`, `form_shape`,
     `contact_email` and their provenance.
   - `import-prospects --csv <backlink-prospects-<domain>-<stamp>.csv> --out <snapshot.json>`
     - reads the UI export, which DOES carry `is_gap`. That value is passed through,
     because it encodes upstream judgements this tool cannot reproduce (a
     documentation page, a competitor the flag misses). Recomputation would
     resurrect those rows as actionable targets. `--dry-run` reports the mapping:
     `already_have` -> `acquired`, `is_gap` AND-ed with `valid`, and every column
     the closed contract cannot hold, listed rather than silently dropped.
3. `validate-snapshot --file <export>` - verify schema version, hash and center domain.
4. `build-manifest --snapshot <export> --history <file> --history-dir <dir>` - read the
   snapshot, join prose-derived contacts, apply admission and cadence rules, render
   per-row outbound copy from the template set, then write `delivery-manifest.json`
   and an append-only `receipts.jsonl`. History can come from a primary file and/or a
   directory of files; all are aggregated.
5. `render-template --channel-type <type> --context-json <obj>` - preview the exact
   outbound copy a row would receive, without writing anything. Use to review a
   template before relying on it.
6. `lookup --target-domain <domain> [--history ...] [--history-dir ...]` - reconcile
   recorded sends/observations for one target domain across all history sources.
7. A human reviews the rows. `ready_for_review` has a channel *and* a known landing
   page; `ready_to_submit` has a channel but no landing page yet, which is normal for
   a directory that only publishes a listing after it accepts you. `no_channel` means
   discovery observed the site and found neither a form nor an address - that row is
   finished, not pending. No tool acts on any of them automatically.
8. `draft --manifest <delivery-manifest.json> --out <dir> [--from <addr>]
   [--ceiling 0.80] [--domain <d>] [--dry-run]` - turn the manifest's email rows into
   `.eml` files. Subject and body come from the channel template; provenance travels
   in `X-BDL-*` headers and a deterministic `Message-ID`. Templates carry several
   bodies per channel, and the draft picks the first one whose 5-word-shingle
   similarity to copy already drafted **in this batch** stays under the ceiling -
   measured with domains and URLs masked, because one template with the domain
   swapped still scores ~0.76 raw and is the same paragraph to a duplicate detector.
   A near duplicate is skipped with the closest domain and the score, never merged.
   Nothing is transmitted: a draft is a file.
9. `record --target-domain <d> --channel <form|email> --action <...>` - write the
   action a human actually performed. Append-only and idempotent: a repeated event
   id is refused. A `sent` needs `--provider-id`; a `submitted` needs `--evidence`;
   a `reply` needs `--reply-class yes|no|pay|reciprocal`. `pay` and `reciprocal`
   are recorded and flagged `compliance: refused` (R-510) - the domain is closed,
   not negotiated.
10. `poll-receipts --provider <resend|postmark> [--auto-confirm] [--dry-run]` - ask the
   sending provider what happened to each unresolved send. One GET per message, only
   for sends inside `BDL_RECEIPT_WINDOW_DAYS` that carry a provider id and have no
   resolving receipt yet. `delivered` and `bounced` become events; `pending` and
   `unknown` write nothing. The receipt ledger goes to `transport-receipts.jsonl`.
   Without `--auto-confirm` (and with `BDL_AUTO_CONFIRM=false`) the events are only
   queued to `receipt-proposals.jsonl` for review. A 401, 404, 429, 5xx, timeout or
   unrecognised status word is `unknown`, never a bounce - a rate-limited API must
   not close a live mailbox for 180 days. `--map-status <word>` prints the mapping
   for one provider status without calling anything.
11. `status [--manifest <file>] [--channel <form|email>] [--target-domain <d>]` -
    fold the event log into one current state per `(target_domain, channel)` and
    answer "in delivery / delivered / live". Every state carries a `next_action`
    and, where a timer applies, `window.due_at`. Seeding from a manifest turns
    untouched (non-blocked) targets into `not_started` rows so nothing is invisible.
12. `forms list|show|register|check|lookup` - the form fingerprint registry. A form is
    identified by `sha256(method + action path + sorted field names)`, never by
    domain alone: a site's contact form and its submission form have different
    fields. An unseen fingerprint reports `needs_human_verify`; `register` demands
    `--reviewed-by`, and a CAPTCHA or login-gated form is stored as `manual_only`.
    Each entry also carries a `slot_map` (wire field -> slot) and a `signature`
    (wire names with their counters blanked), which is what lets a reviewed map be
    reused on another site running the same plugin - see *Filling a submission form*.
13. `forms prefill --manifest <file> [--snapshot <file>] [--out <dir>]` then
    `forms adopt --file <receipt> --reviewed-by <who>` - compose value packs for the
    browser userscript and store the map a human submission produced.
14. `plan-window --submitted-at <iso>` - compute the T+1/3/7/14/30 day fetch schedule.
15. `classify --outcome ... --http-status ...` - record an observation. A 403, timeout
    or robots refusal is `unknown`, never `absent`.
16. `observe --target-url <page> --own-url <our-url>` - the local L4 detector. A single
    read-only GET; finds an anchor to the own host (`found`), reports `absent` only on
    a 2xx page with no such anchor, and reports `unknown` on any fetch failure (403,
    timeout, robots). Use `--record <file>` to append the observation as JSONL.
17. `scan --manifest <delivery-manifest.json> [--auto-confirm] [--out <dir>]` - the
    auto-scan. For every observable row (has `target_url` + `own_url`), run the L4
    detector, append the result to `observations.jsonl`, and classify. With
    `--auto-confirm` (or `BDL_AUTO_CONFIRM=true`), a `found` link is marked `live`
    locally and a confirm proposal is queued in `confirm-proposals.jsonl`. Marking the
    shared link-ledger `live` is still a separate, human-confirmed SEXTANT step.
18. `replies --source <.eml|.mbox|dir> [--manifest <file>] [--auto-confirm]` - read
    exported replies, match each to its target domain, and queue a classification
    proposal per reply. See *Classifying replies*.

## Projection states

`status` reports one state per `(target_domain, channel)`:

| State | Meaning | Next action |
|---|---|---|
| `not_started` | target admitted, no delivery attempted | enrich the channel |
| `queued` | approved, waiting for a human to transmit | human transmit |
| `sent` | provider accepted the mail | poll receipts |
| `delivered` | provider confirms delivery | wait for a reply |
| `submitted` | form posted with evidence | observe the page |
| `replied` | a human replied yes | observe, then confirm |
| `declined` / `declined_paid` / `declined_reciprocal` | closed, do not retry | none |
| `bounced` | provider rejected the mail | try another address |
| `contact_lost` | address inside its cooldown | wait out the cooldown |
| `unanswered` | window closed with no reply | close or re-approach later |
| `unanswered_no_receipt` | no provider receipt at all | unknown, never resend blindly |
| `live` | link observed on the page (L4) | confirm in link-ledger |

`live` is the only success state, and it can only come from an observation. A send
proves transmission (L1); it does not prove the link exists (L4). An absence inside
the window is never a failure (R-512 / R-513).

## Transport receipts (L2)

`sent` proves a provider accepted a message and can name it. `poll-receipts` turns
that name into an answer: delivered, bounced, or still unknown.

| Provider word | Ledger result | Why |
|---|---|---|
| `delivered`, `opened`, `clicked` | `delivered` event | an open cannot precede arrival |
| `bounced`, `complained`, `hardbounce`, `spamcomplaint` | `bounced` event | the address enters the contact-lost cooldown |
| `delivery_delayed`, `queued`, `softbounce` | `pending`, no event | still in flight, not a verdict |
| 401 / 403 / 404 / 429 / 5xx / timeout / unknown word | `unknown`, no event | a bad read is not a bounce |

Three constraints hold:

- **Bounded.** Only sends inside `BDL_RECEIPT_WINDOW_DAYS` with a provider id and no
  resolving receipt are queried, so polling is idempotent and cannot fan out.
- **Once per fact.** A resolution is keyed on `(provider id, action)`, not on the
  provider's imprecise timestamps; a repeated poll writes nothing new.
- **Not a promotion.** A receipt is L2. It never sets `live`, never touches the
  shared link-ledger, and never authorises a resend to a bounced address before the
  cooldown ends.

## Filling a submission form

A form submission cannot be automated end to end and stay honest: a human judges the
CAPTCHA, the terms, and the acknowledgement page. Everything around that judgement
can be automated, and it is:

```
forms prefill  ->  pack (values per slot)  ->  userscript fills  ->  human clicks
                                                          |
               forms adopt  <-  receipt (fields + slot map + response URL)
                    |
               record --action submitted --evidence <response URL>
```

`forms prefill` writes one pack per submittable row. Values come from the manifest
row and the channel template; `message` is picked from the same composed body
variants as a draft, with the same 5-word-shingle ceiling measured against the copy
already packed **in this batch**, so twenty forms in one batch do not carry twenty
identical paragraphs. A pack never invents a value: no `BDL_FROM_EMAIL`, no landing
URL, or a `phone` slot stops at a recorded gap instead of a plausible-looking fake.
`--snapshot` supplies `form_shape` and `form_fields` from discovery; without it every
pack is `mapping: none` and the browser guesses from labels.

The declaration `mapping` is the honest part of a pack:

| `mapping` | Meaning | What fills it |
|---|---|---|
| `exact` | this method + action + field set was reviewed before | the stored `slot_map` |
| `shape` | another site, same form shape *and* same wire-name signature | that site's `slot_map` |
| `none` | nothing to reuse | browser heuristics, then a human |

`shape` reuse deliberately requires the signature as well as the shape. A shape says
"this form has name, email, url, message"; it does not say which wire field carries
which. Two WPForms sites both name their fields `wpforms[fields][0..3]`, so the
mapping transfers; two bespoke forms with the same slots do not, and pretending
otherwise writes a pitch into a "Order number" box.

The userscript (`assets/userscript/bdl-prefill.user.js`, paste into Tampermonkey or
Violentmonkey) fills, outlines every field it touched with a dashed border and the
slot name, lists what it left for the human, and never clicks submit, never solves a
CAPTCHA and never fills a honeypot. After the human submits, the acknowledgement page
is captured as a receipt: field names, the slot map, the values used, the response URL
and a fingerprint of the response text.

`forms adopt` refuses a receipt that was not a human click (R-520), has no slot map,
or has no response URL - a submission with no acknowledgement page is not evidence
(L1). What it stores is a submission acknowledgement, not a live link: `live` remains
the L4 observer's alone. It then prints the exact `record --action submitted` command,
so the event still enters the log through the same reviewed door.

## Classifying replies

The last manual step in the loop is reading what came back. What this automates is
the part before the judgement: matching each message to its target domain and naming
the signals in the text. What it does not do is decide.

Matching, best evidence first:

1. `In-Reply-To` / `References` names one of our `Message-ID`s - the local part is the
   manifest's idempotency key, which resolves to one row exactly;
2. the `From` domain is a target domain already known to the loop;
3. nothing matches: the message is reported under `unmatched` with a hint, never guessed.

Classification produces a `reply_class` with the **matched phrases** (not the regexes)
and a confidence:

| Class | Confidence | Consequence |
|---|---|---|
| `yes` | high needs two distinct signals | `--auto-confirm` may write the event |
| `no` | high needs two distinct signals | `--auto-confirm` may write the event |
| `pay` / `reciprocal` | any | always a proposal: these close a domain (R-510) |
| a delivery status notification | - | proposed as `bounced` (L2), with the DSN `Status` code |

Negation is handled before matching: "there is no fee for non-commercial sites" is a
`yes`, not a `pay`. A "we'd love to add it, there is a $50 listing fee" lands on
`pay`, because the offer and the fee are the same sentence.

Every classification leaves as a **proposal** to `reply-proposals.jsonl` with the
signals shown, and the event is still written by a human command:

```
record --action reply --reply-class yes --address ... --note "confirmed manually"
```

`--auto-confirm` writes only `yes`/`no` at high confidence, never a `pay`, never a
`reciprocal`, and never a bounce - all three close things, and a classifier's error
in closing things is a door shut on a site that was about to link.

## Manifest statuses

`ready_for_review` (materials complete, human gate pending) - `needs_enrichment`
(missing landing page or delivery channel, recorded as `gaps`) - `deferred` (90-day
repeat window) - `blocked` (competitor, acquired, upstream `is_gap=false`,
reciprocal, unreachable, excluded contact, existing live link).

`ready_for_review` and `needs_enrichment` are entry states, not progress: they say
whether the materials are complete, not whether anything was delivered. Progress
lives in `status`, never in a manifest.

## Layered design

- Entry layer: `bdl/cli.py` parses arguments and formats JSON only.
- Service layer: `normalize`, `cadence`, `window`, `pipeline`, `templates`, `scan`,
  `state`, `record`, `receipts` hold business rules and never touch HTTP or file
  handles directly (the network call in `scan`/`observe`/`receipts` is injected
  behind a `Fetcher` / `Reader` callable).
- Adapter layer: `snapshot`, `csv_import`, `prospector_export`, `prose`, `outbox`,
  `observation`, `events`, `forms`, `provider_events` own all I/O.
- Cross-cutting: central environment validation, typed errors with a global handler,
  structured JSON logs with `batch_id`, boundary input validation, idempotency keys,
  atomic writes and append-only receipts and events.

## References

| Load when | File |
|---|---|
| Field mapping, state machine, L1-L4, observation window | `references/state-and-evidence.md` |
| R-520 / R-530 decision and rule-change cost | `references/rule-boundary.md` |
| Prose extraction, exclusions, provenance, drift | `references/prose-extraction.md` |
| Upstream snapshot contract C and type mapping | `references/upstream-contract.md` |

Chinese mirrors are `SKILL.zh.md` and `references/*.zh.md`. English is the source of
truth; code and English documents contain no Hanzi.
