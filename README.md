# Backlink Delivery Loop

Prepare and record backlink outreach for a center domain, then observe whether a
link actually goes live. Python standard library only. No network calls, no form
submissions, no mail transmission.

English is the source of truth. `README.zh.md` and every `*.zh.md` are Chinese
mirrors; code, English documents and English comments contain no Hanzi.

- Repository: `backlink-delivery-loop` (short local name, matching `mqtt-coop`)
- Remote name: `tokencoup-skills-backlink-delivery-loop`
- Upstream: `tokencoup-skills-backlink-prospector`
- Source of truth for link state: SEXTANT `07-links/link-ledger.json`

---

## 1. What this package does

| Does | Does not |
|---|---|
| Consume versioned prospect snapshots (contract C) | Read the upstream store file directly |
| Structure contacts from operator prose with full provenance | Confirm an address without a human |
| Apply admission, cadence and repeat-window rules | Register accounts or log in |
| Write a review-only `delivery-manifest.json` | Send a pitch or submit a form |
| Append tamper-evident JSONL receipts | Write `link-ledger.state` |
| Compute the submitted-to-live observation schedule | Fetch target pages (rule-backed worker, future) |
| Build a standalone distribution zip | Auto-install into `.workbuddy/skills` |

`BDL_AUTO_TRANSMIT=true` is rejected at startup. This implements the confirmed
R-520 decision: keep the rule text unchanged, use R-530 tier B (one review per
batch) for class U targets.

## 2. Confirmed design decisions

1. Local directory uses the short name; the remote keeps the `tokencoup-skills-` prefix.
2. Standalone distribution package: no symlink, no automatic skill loading.
3. Integration contract C: versioned snapshot export, read-only consumption.
4. First local git commit is part of this delivery.
5. Bilingual docs, schema, licence, ignore rules and the upstream contract are shipped.
6. R-520 tier 1 (R-530 instance override + tier B) is confirmed; R-520 is not edited.

## 3. Requirements

- Python 3.10 or newer
- No third-party packages; no `pip install` needed
- No environment file is committed. Copy `.env.example` to `.env` and keep it local.

## 4. Quick start

```bash
cd backlink-delivery-loop

export BDL_HOME=$PWD
export BDL_CENTER_DOMAIN=soundbuttons.cc
export BDL_OWN_SITE_BASE=https://soundbuttons.cc
export BDL_PROSE_DIR=$PWD/assets/prose
export BDL_OUT_DIR=$PWD/data/out
export BDL_AUTO_TRANSMIT=false
export BDL_TIER=B

python3 bin/delivery doctor
python3 bin/delivery validate-snapshot --file assets/prospect-snapshot.example.json
python3 bin/delivery build-manifest \
  --snapshot assets/prospect-snapshot.example.json \
  --history assets/send-history.example.json

python3 bin/delivery plan-window --submitted-at 2026-11-14T12:00:00Z
python3 bin/delivery classify --outcome absent --http-status 403
python3 bin/delivery package --out dist
```

On the sample fixture the manifest reports 9 rows: 3 `ready_for_review`,
1 `needs_enrichment`, 1 `deferred`, 4 `blocked`. Nothing is transmitted.

## 5. Commands

| Command | Purpose |
|---|---|
| `doctor` | Validate config, print paths and policy state |
| `export-snapshot --store ... --out ...` | Contract-C producer: read a prospector store, emit a versioned snapshot |
| `import-prospects --csv ... [--out ...] [--dry-run]` | Read a prospector CSV export (the richer surface: it carries `is_gap`), report the mapping, emit a snapshot |
| `validate-snapshot --file ...` | Enforce the snapshot contract |
| `build-manifest --snapshot ... [--history ...] [--history-dir ...]` | Produce the review artefact + receipt; embeds rendered copy per row |
| `render-template --channel-type ... --context-json ...` | Preview outbound copy a row would receive |
| `lookup --target-domain ... [--history ...] [--history-dir ...]` | Reconcile recorded sends for one target domain |
| `record --target-domain ... --channel form\|email --action ...` | Record one delivery action; append-only and idempotent |
| `status [--manifest ...] [--channel ...] [--target-domain ...]` | Fold the event log into a current state per domain and channel |
| `forms list\|show\|register\|check` | Form fingerprint registry; a first-seen form needs a named human reviewer |
| `plan-window --submitted-at ...` | T+1/3/7/14/30 day schedule |
| `classify --outcome ... --http-status ...` | Map one observation to found/absent/unknown |
| `observe --target-url ... --own-url ... [--record ...]` | Local L4 detector (read-only GET) |
| `scan --manifest ... [--auto-confirm] [--out ...]` | Auto-scan target pages, record observations, optionally queue confirm proposals |
| `package --out ...` | Build `dist/backlink-delivery-loop.zip` |

All commands print structured JSON to stdout. Logs are structured JSON on stderr
with a `batch_id`; errors are normalised JSON with an error `code` and never expose
a traceback.

### Configuration (env vars)

`BDL_HOME`, `BDL_SNAPSHOT_DIR`, `BDL_PROSE_DIR`, `BDL_OUT_DIR`, `BDL_HISTORY_DIR`,
`BDL_TEMPLATES_DIR`, `BDL_OBSERVATION_DIR`, `BDL_RECORDS_DIR`, `BDL_FORMS_DIR`,
`BDL_CENTER_DOMAIN`, `BDL_OWN_SITE_BASE`, `BDL_TIER` (`A`/`B`), `BDL_AUTO_TRANSMIT`
(must stay `false`), `BDL_AUTO_CONFIRM` (default `false`), `BDL_DEFERRAL_WINDOW_DAYS`
(default `90`), `BDL_DAILY_CEILING` (default `100`), `BDL_RECEIPT_WINDOW_DAYS`
(default `30`), `BDL_CONTACT_LOST_DAYS` (default `180`), `BDL_LOG_LEVEL`.
See `.env.example` for the full list.

### Delivery channel is not the same axis as channel type

`channel_type` (asset, guest_post, directory, community, journalist, other) decides
the copy. `channel` (`form`, `email`) decides how it is delivered, and it is a
property of the landing page: one domain can offer both, in which case a manifest row
carries `channels: ["form", "email"]` and `status` tracks the two separately.

### Status projection

Manifests are photographs; `status` is the film. `record` appends one event per
action a human actually performed, and `status` folds those events into a current
state per `(target_domain, channel)`:

```
record --target-domain mixkit.co --channel email --action sent \
       --address hello@mixkit.co --provider-id msg_123
record --target-domain mixkit.co --channel email --action bounced --address hello@mixkit.co
status --manifest data/out/delivery-manifest.json
```

A `sent` needs a provider id; a `submitted` needs evidence; a `reply` needs a class.
`pay` and `reciprocal` replies are stored with `compliance: refused` (R-510) so the
domain is closed rather than negotiated. A bounce puts the *address* into a 180-day
cooldown without closing the domain's other channel. No receipt inside 30 days is
`unanswered_no_receipt` - unknown, never "failed", and never a reason to resend.

### Form fingerprint registry

A form is identified by `sha256(method + action path + sorted field names)`, not by
domain: a site's contact form and its submission form have different fields. An
unseen fingerprint returns `needs_human_verify`; `forms register` requires
`--reviewed-by` and stores the field map, the success selector and any
CAPTCHA/login gate (`manual_only`). Nothing auto-registers a form, and a human still
clicks submit.

### Templates

Built-in copy for every `channel_type` ships with the package. Override any channel
via `BDL_TEMPLATES_DIR`: `templates.json` overrides all centres, `<center_domain>.json`
overrides one. See `assets/templates.example.json`. Rendering rejects undefined
placeholders and CJK in email copy (R-522).

### Auto-confirm

`scan --auto-confirm` (or `BDL_AUTO_CONFIRM=true`) marks a `found` link `live` in the
local observation ledger and queues a confirm proposal. It never writes the shared
link-ledger - promoting the shared state is a separate, human-confirmed SEXTANT step.

## 6. Outputs

`data/out/delivery-manifest.json`

```json
{
  "center_domain": "soundbuttons.cc",
  "batch_id": "...",
  "policy": {"tier": "B", "auto_transmit": false, "human_review_required": true},
  "summary": {"total": 9, "by_status": {"...": 0}},
  "rows": [{"target_domain": "beebom.com", "status": "ready_for_review", "gaps": []}],
  "gaps": []
}
```

`data/out/receipts.jsonl` is append-only. Both are derived artefacts; neither is the
ledger. `data/` is gitignored.

## 7. Architecture

```
bin/delivery                 entry point (thin launcher)
bdl/cli.py                   controllers: parse args, format JSON, global error handler
bdl/services/                business rules, no HTTP types, no file handles
  normalize.py               admission, type mapping, idempotency keys, CJK guard
  cadence.py                 90-day window, per-domain 24h ceiling
  window.py                  observation schedule, unknown != absent
  pipeline.py                orchestration
bdl/adapters/                all I/O
  snapshot.py                contract C validator
  prose.py                   prose extraction with provenance
  outbox.py                  atomic writes, append-only receipts, history reader
bdl/config.py                central environment validation, fail fast
bdl/errors.py                typed errors
bdl/log.py                   structured JSON logging with masking
bdl/models.py                plain dataclasses
bdl/package.py               distribution zip
```

Cross-cutting: boundary input validation, typed errors with a global handler,
atomic writes, idempotency keys, structured logs, no secrets in YAML/JSON.

## 8. Contracts and policy

- `contracts/prospect-snapshot.schema.json` - upstream snapshot, version `1.0.0`.
- `references/upstream-contract.md` - field mapping and why `url` is not `target_url`.
- `references/state-and-evidence.md` - L1-L4, ledger fields, observation window.
- `references/rule-boundary.md` - R-520 / R-530 decision and rule-change cost.
- `references/prose-extraction.md` - extraction, exclusions, drift.

Relevant rules: R-510, R-512, R-513, R-520, R-522, R-530, R-414.

## 9. Tests

```bash
PYTHONPATH=$PWD python3 -m unittest discover -s tests -t . -v
python3 -m compileall -q bdl bin tests
```

122 offline unit and CLI tests cover schema rejection, CSV import mapping, prose
provenance, field mapping, cadence, the observation window, templates, history
reconciliation, the scan/auto-confirm ledger, event idempotency, the state
projection and its two timers, form fingerprints, the transmission gate, packaging
and CLI output.
No test needs network access or mocks a service layer.

## 10. Distribution

`bin/delivery package` writes `dist/backlink-delivery-loop.zip`, excluding `.git`,
`data/`, `out/`, `dist/`, `__pycache__`, `.env`, keys and temporary files. Install
the zip explicitly where the skill is needed; it is not auto-loaded.

## 11. Not implemented (by design)

- Form submission and mail transmission (human gate, R-520 / R-530 tier B). The
  rendered copy is produced for review but never sent.
- Writing `link-ledger.state` (single human writer, R-512). `scan --auto-confirm`
  only marks the local observation ledger `live` and queues a proposal.
- The upstream exporter side of contract C (the prospector store producer inside
  `backlink-prospector` that emits the snapshot this package consumes).
- MX resolution and provider event polling (L2-L4 address levels). `record` accepts
  a receipt a human or a separate poller obtained; it does not fetch one.
- Form prefill and submission. `forms register` stores a verified field map; the
  act of filling and clicking submit stays human.
- Inbound reply parsing. `record --action reply` takes the classification a human
  made; nothing reads a mailbox.

## 12. Licence

MIT. Copyright (c) 2026 hhool.
