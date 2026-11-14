---
name: backlink-delivery-loop
description: This skill should be used when preparing or reviewing a backlink delivery loop for a center domain - consuming versioned prospect snapshots exported by tokencoup-skills-backlink-prospector, structuring contact addresses from operator prose, computing cadence and the submitted-to-live observation window, and writing a review-only delivery manifest backed by link-ledger. It never sends mail and never submits a form.
agent_created: true
---

# Backlink delivery loop

## Purpose

Turn a center domain's prospect pool into a **reviewable delivery manifest** with a
complete audit trail, then observe whether a link actually goes live. The skill is a
standalone distribution package: it is not auto-loaded by the WorkBuddy skill
scanner and must be installed explicitly.

**Hard boundary.** This package performs no network calls, no form submissions and no
mail transmission. `BDL_AUTO_TRANSMIT=true` is rejected at startup. Transmission
stays behind a human review gate (R-520, R-530 tier B on class U targets).

## Invariants

| Term | Definition |
|---|---|
| `center_domain` | Our own center domain, e.g. `soundbuttons.cc`. Resolved from the site slug, never written into a closed ledger row. |
| `target_domain` | The third-party domain that may host the link. |
| Primary key | `(center_domain, target_domain, channel_type, target_url)` |
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
| `BDL_CENTER_DOMAIN` | - | Our center domain (required for most commands). |
| `BDL_OWN_SITE_BASE` | derived | Base URL of the center site. |
| `BDL_TIER` | `B` | `A` or `B`; `A` requires a recorded human authoriser. |
| `BDL_AUTO_TRANSMIT` | `false` | `true` is rejected: transmission stays behind the human gate. |
| `BDL_AUTO_CONFIRM` | `false` | When `true`, a found L4 link is marked `live` in the local ledger and queued as a confirm proposal. The shared link-ledger is never written here. |
| `BDL_DEFERRAL_WINDOW_DAYS` | `90` | Repeat-pitch deferral window per target domain (R-522 cadence). |
| `BDL_DAILY_CEILING` | `100` | Per-target-domain daily ceiling (R-530 instance override). |
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
2. `export-snapshot --store <prospector store-<domain>.json> --out <snapshot.json>` -
   the contract-C producer. Reads a backlink-prospector store and emits a versioned
   snapshot. The consumer never touches the prospector layout; this command is the
   only bridge.
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
7. A human reviews each `ready_for_review` row. No tool acts on it automatically.
8. `plan-window --submitted-at <iso>` - compute the T+1/3/7/14/30 day fetch schedule.
9. `classify --outcome ... --http-status ...` - record an observation. A 403, timeout
   or robots refusal is `unknown`, never `absent`.
10. `observe --target-url <page> --own-url <our-url>` - the local L4 detector. A single
    read-only GET; finds an anchor to the own host (`found`), reports `absent` only on
    a 2xx page with no such anchor, and reports `unknown` on any fetch failure (403,
    timeout, robots). Use `--record <file>` to append the observation as JSONL.
11. `scan --manifest <delivery-manifest.json> [--auto-confirm] [--out <dir>]` - the
    auto-scan. For every observable row (has `target_url` + `own_url`), run the L4
    detector, append the result to `observations.jsonl`, and classify. With
    `--auto-confirm` (or `BDL_AUTO_CONFIRM=true`), a `found` link is marked `live`
    locally and a confirm proposal is queued in `confirm-proposals.jsonl`. Marking the
    shared link-ledger `live` is still a separate, human-confirmed SEXTANT step.

## Statuses

`ready_for_review` (materials complete, human gate pending) - `needs_enrichment`
(missing landing page or delivery channel, recorded as `gaps`) - `deferred` (90-day
repeat window) - `blocked` (competitor, acquired, reciprocal, unreachable, excluded
contact, existing live link).

## Layered design

- Entry layer: `bdl/cli.py` parses arguments and formats JSON only.
- Service layer: `normalize`, `cadence`, `window`, `pipeline`, `templates`, `scan`
  hold business rules and never touch HTTP or file handles (the network call in
  `scan`/`observe` is injected behind a `Fetcher`).
- Adapter layer: `snapshot`, `prose`, `outbox`, `observation` own all I/O.
- Cross-cutting: central environment validation, typed errors with a global handler,
  structured JSON logs with `batch_id`, boundary input validation, idempotency keys,
  atomic writes and append-only receipts.

## References

| Load when | File |
|---|---|
| Field mapping, state machine, L1-L4, observation window | `references/state-and-evidence.md` |
| R-520 / R-530 decision and rule-change cost | `references/rule-boundary.md` |
| Prose extraction, exclusions, provenance, drift | `references/prose-extraction.md` |
| Upstream snapshot contract C and type mapping | `references/upstream-contract.md` |

Chinese mirrors are `SKILL.zh.md` and `references/*.zh.md`. English is the source of
truth; code and English documents contain no Hanzi.
