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
| `validate-snapshot --file ...` | Enforce the snapshot contract |
| `build-manifest --snapshot ... --history ...` | Produce the review artefact + receipt |
| `plan-window --submitted-at ...` | T+1/3/7/14/30 day schedule |
| `classify --outcome ... --http-status ...` | Map one observation to found/absent/unknown |
| `package --out ...` | Build `dist/backlink-delivery-loop.zip` |

All commands print structured JSON to stdout. Logs are structured JSON on stderr
with a `batch_id`; errors are normalised JSON with an error `code` and never expose
a traceback.

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

39 offline unit and CLI tests cover schema rejection, prose provenance, mapping,
cadence, the observation window, the transmission gate, packaging and CLI output.
No test needs network access or mocks a service layer.

## 10. Distribution

`bin/delivery package` writes `dist/backlink-delivery-loop.zip`, excluding `.git`,
`data/`, `out/`, `dist/`, `__pycache__`, `.env`, keys and temporary files. Install
the zip explicitly where the skill is needed; it is not auto-loaded.

## 11. Not implemented (by design)

- Form submission and mail transmission (human gate, R-520 / R-530 tier B).
- Fetching `target_url` for L4 (needs a rule-backed observation worker).
- Writing `link-ledger.state` (single human writer, R-512).
- The upstream exporter side of contract C.
- MX resolution and provider event polling (L2-L4 address levels).

## 12. Licence

MIT. Copyright (c) 2026 hhool.
