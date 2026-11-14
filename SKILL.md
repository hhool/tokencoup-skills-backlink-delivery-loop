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

## Workflow

1. `doctor` - validate configuration, confirm the transmission gate is closed.
2. `export-snapshot --store <prospector store-<domain>.json> --out <snapshot.json>` -
   the contract-C producer. Reads a backlink-prospector store and emits a versioned
   snapshot. The consumer never touches the prospector layout; this command is the
   only bridge.
3. `validate-snapshot --file <export>` - verify schema version, hash and center domain.
4. `build-manifest --snapshot <export> --history <send-history.json>` - read the
   snapshot, join prose-derived contacts, apply admission and cadence rules, then
   write `delivery-manifest.json` and an append-only `receipts.jsonl`.
5. A human reviews each `ready_for_review` row. No tool acts on it automatically.
6. `plan-window --submitted-at <iso>` - compute the T+1/3/7/14/30 day fetch schedule.
7. `classify --outcome ... --http-status ...` - record an observation. A 403, timeout
   or robots refusal is `unknown`, never `absent`.

## Statuses

`ready_for_review` (materials complete, human gate pending) - `needs_enrichment`
(missing landing page or delivery channel, recorded as `gaps`) - `deferred` (90-day
repeat window) - `blocked` (competitor, acquired, reciprocal, unreachable, excluded
contact, existing live link).

## Layered design

- Entry layer: `bdl/cli.py` parses arguments and formats JSON only.
- Service layer: `normalize`, `cadence`, `window`, `pipeline` hold business rules and
  never touch HTTP or file handles.
- Adapter layer: `snapshot`, `prose`, `outbox` own all I/O.
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
