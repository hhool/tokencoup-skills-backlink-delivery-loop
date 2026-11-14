# Upstream contract C: prospect snapshot

## 1. Decision

Target candidates come from `tokencoup-skills-backlink-prospector`. The integration
is **contract C**: the upstream exports a versioned snapshot; this package only
reads it. There is no direct read of the upstream store file, no shared database and
no runtime dependency on the upstream HTTP service.

| Option | Assessment |
|---|---|
| A - read `data/store-<center_domain>.json` directly | Zero dependencies, but couples to a private file layout; rejected |
| B - `GET /api/store` | Stable contract, but requires a running service, loopback binding, timeouts and auth; kept only as an optional online path |
| **C - versioned snapshot export (chosen)** | Decoupled, versionable, auditable, replayable |

## 2. Schema

`contracts/prospect-snapshot.schema.json` (JSON Schema 2020-12). The runtime
validator in `bdl/adapters/snapshot.py` enforces the same rules using only the
standard library:

- `schema_version` must equal `1.0.0`; an unknown version is refused, not ignored.
- Unknown keys at any level are refused (`additionalProperties: false` semantics).
- `source.content_hash` must be `sha256:<64 hex>`.
- `center_domain` must match the requested center domain.
- `prospects[].domain` must be unique.
- The snapshot's own sha256 is computed and stored as `file_hash` in the manifest.

A malformed snapshot raises `SnapshotError`, a typed error with a normalised JSON
payload and no traceback.

## 3. Field semantics

| Upstream field | Use here | Do not |
|---|---|---|
| `domain` | `target_domain` part of the primary key | |
| `url` | `candidate_url`, a site entry point | **never** promote to `target_url` |
| `type_key` | map to `link-ledger.channel_type` | |
| `priority` | scheduling weight only | never write into the ledger |
| `http_status` | reachability; `null` is a `gap` | never guess a value |
| `acquired` | skip | |
| `is_competitor` | skip | |
| `is_gap` | admission hint | |
| `note`, `note_en` | local operator data | never put into outbound copy |

`link-ledger.target_url` is the third-party page where the link will appear. It is
derived from prose evidence (`target_url_hint`) and stays `null` with a `gaps` entry
until confirmed. This is the single most important mapping rule: using `url` here
would make the L4 verification fetch the wrong page forever.

## 4. type_key to channel_type

| type_key | channel_type |
|---|---|
| `industry_media` | `asset` |
| `guest` | `guest_post` |
| `resource` | `asset` |
| `directory` | `directory` |
| `community` | `community` |
| `social` | `community` |
| `partner` | `other` |
| `authority` | `asset` |
| `digital_pr` | `journalist` |
| `reciprocal` | **excluded** |

`reciprocal` is excluded because the upstream README itself warns that reciprocal
linking used to manipulate ranking is a link scheme, and R-510 is a white-hat hard
constraint. Unknown `type_key` values fall back to `other`, never to a guess.

## 5. Responsibilities of the exporter (upstream side)

Not implemented in this repository. The upstream should:

1. Export `data/store-<center_domain>.json` into the snapshot shape.
2. Set `center_domain`, `schema_version`, `generated_at`, `source.repo`,
   `source.file` and `sha256` of the store file.
3. Write to `data/snapshots/<center_domain>-<timestamp>.json` in this repository,
   or to any path passed with `--snapshot`.
4. Bump `schema_version` on any breaking field change; consumers reject unknown
   versions rather than silently dropping fields.

Missing data is reported as `unavailable` (upstream) or `gap` (here); neither side
substitutes 0, false or an estimate.
