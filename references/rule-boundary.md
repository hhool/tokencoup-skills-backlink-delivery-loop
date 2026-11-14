# R-520 decision and the cost of changing a rule

## 1. What R-520 actually forbids

Source: `repos/wb-seo-ops/rules/R-520-link-stage-automation.md` and its `rules/zh/`
mirror.

- Machine may: read a public page for link presence, compute cadence, draft copy,
  open a page at the next step.
- Machine may not: register an account, confirm an email, submit a profile/post/pitch,
  log in on a person's behalf.
- **Boundary clause**: the rule decides only who may act - a human, **or the machine
  with a human's authorisation recorded**.
- Criteria: the `allowed` verdict is recorded with the operation, the channel id and
  the authorising person. "The script decided" is not evidence.
- Two standing limits: public pages only; **one channel per command** (two channels
  is an unapproved batch); a refusal is never retried on a schedule.

R-520 is therefore an attribution requirement, not a blanket ban. The existing
`AGENT-PLAYBOOK.md` allows `autonomy=auto` form submission through exactly this
channel, while keeping mail and `state` with a human.

## 2. Confirmed decision: tier 1, do not edit R-520

| Tier | Action | Outcome |
|---|---|---|
| 0 | Keep authorisation records + one channel per command | Current posture, no change |
| **1 (confirmed)** | Declare the R-530 instance override in `console.config.yaml`; use tier B (one review per batch, one receipt per item) | Mail moves from per-message clicks to per-batch clicks; on this instance R-522's 1/24h and 5/30d are replaced by 100 addresses per domain per 24h; the 90-day window stays |
| 2 | Tier C unattended scheduled sending | **Forbidden by R-530** for class U |

R-530 states: "Tier C on class U is **not enabled** by this rule. `compliance.gate`
hard-blocks automated link creation as a non-relaxable mapping of an external
platform policy ... An internal override cannot waive an external policy."

Editing the R-520 body would grant no new permission, because it already allows
authorised machine action. The real constraint is the U/tier-C combination plus the
external platform policy. Unattended sending needs a separate written ruling and a
`compliance.gate` change - not a text edit.

For tier C, R-530 replaces the named click with an append-only standing
authorisation: authorising person, rule id, batch id, timestamp. Tiers A and B keep
the named click.

## 3. How this package enforces the decision

- `bdl/config.py::Config.from_env` rejects `BDL_AUTO_TRANSMIT=true` at startup.
- `bdl/cli.py` has no send or submit command at all.
- `bdl/services/pipeline.py` writes `policy.auto_transmit: false` and
  `human_review_required: true` into every manifest.
- `outbox.py` writes local artefacts only; receipts are append-only and replayable.
- `assert_outbound_copy` refuses CJK and unreplaced placeholders (R-522).

## 4. If a SEXTANT rule must change

From `sextant-rule-and-tool`, in order; a wrong order turns `verify`, `tools` or
`tools-doc` red on different axes.

1. Write the EN rule and its `rules/zh/` mirror with **byte-identical frontmatter**
   (keys: `id name name_en when when_en inputs outputs evidence on_missing weight
   tools`). The EN body may not contain a single Hanzi.
2. If a script performs the rule's work, register it in
   `repos/wb-seo-hub/tools/registry.json` first (`status: in_use` needs real
   `used_by` paths and an ISO `verified_at`), then put its id in both frontmatters.
3. Bump `repos/<repo>/VERSION` and the `version:` line in both SKILL files.
4. Add a bilingual CHANGELOG entry.
5. Re-render `docs/10` in both languages with `dump_tools.py`, hand-fix the header
   tool count, and grep the old number across docs/09, 11, 12 and README.zh.md.
6. Gates in order: `./bin/sextant tools` -> `tools-doc` -> `verify`. Run `verify` in
   the background (about 50 s); `boss_check` takes about 30 minutes.
7. Sync the installed copy with `./install.sh ~/.workbuddy/skills all` (about 11
   minutes, background) or the gates read the package tree while the console reads
   a stale copy.

Adding a contract or a `state` enum value is a contracts minor bump: version matrix
(including `repo_versions`), twelve `contracts.lock` files, every SKILL frontmatter,
CHANGELOGs, `test_contracts.py::SchemaSetTests.PUBLISHED` and the package docs.

## 5. Other standing constraints

- R-510: white-hat, zero payment, no bulk templates, no fabricated personas, no
  paid links, no PBN, no automated registration.
- R-522: count pitches, not rows; `directory/listing/profile/registry` rows spend no
  mail budget; CJK is prohibited; each body must name the page it concerns.
- R-530: address levels L1 syntax, L2 domain+MX, L3 `provider_id`, L4 `last_event`;
  an unchecked address is a `gap`, never 0 and never a failure.
- R-414: a receipt must be real.
- The seasonal freeze (until 2026-11-22) freezes structural site changes. This
  package does not touch site head, but the entry point still checks `window_state`.
