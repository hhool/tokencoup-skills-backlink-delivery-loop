# Prose to structured contact addresses

## 1. Why a dedicated layer exists

`link-ledger` has no `contact_email` field and is a closed schema. Contact data lives
in operator prose. A real `TODO-backlink.md` contains all of the following at once:

- real addresses in a mail-track table, e.g. `contact@beebom.com`;
- a `/contact` form path and a `https://.../contact` URL in a form-track table;
- a literal `contact form / email` placeholder that is not an address;
- a "do not" section excluding support-only desks;
- frozen domains inside a 90-day repeat window;
- a "landing page" column holding **our own** page path, i.e. `own_url`.

A bare email regex therefore returns valid addresses, placeholders, explicitly
excluded addresses and frozen addresses together. Extraction must read context and
all results require human confirmation.

## 2. Implemented pipeline

`bdl/adapters/prose.py` implements:

1. **Source allowlist.** Scan a configured directory for `.md` / `.txt` files; skip
   hidden files and files outside the directory. A directory scanner must never read
   its own output.
2. **Table-aware extraction.** Parse Markdown tables, map columns by header keywords,
   and read target, entry and landing-page cells separately. A second pass catches
   emails outside tables.
3. **Exclusion detection.** Section headings and row text are checked against
   exclusion markers, in both English and Chinese (stored as escapes so the codebase
   stays ASCII-only).
4. **Ambiguity detection.** Placeholders such as `no public email` produce
   `ambiguous`, not a candidate.
5. **Provenance.** Every record carries `source_file`, `line`, `excerpt` and a
   `content_hash` (sha256 of the source file). A changed hash invalidates the record;
   the old one must not be carried forward silently.
6. **Human gate.** Extraction yields `candidate`, `excluded` or `ambiguous`. Nothing
   in this package promotes a candidate to confirmed.
7. **Validation.** L1 syntax and L2 domain/MX are the admission levels. An address
   that cannot be checked is a `gap`. L3 `provider_id` and L4 `last_event` are
   observed after a send that this package does not perform.
8. **Deduplication.** Group by `target_domain`, never by page URL.

## 3. Record shape

```
target_domain, address, form_url, target_url_hint, own_url_hint,
state (candidate | excluded | ambiguous), reason,
source_file, line, excerpt, content_hash
```

The derived list is never written into `link-ledger`. Runtime identifiers such as
`provider_id` eventually land in the ledger's `notes`.

## 4. Boundaries

- Prose is a derived source; `link-ledger` is the source of truth. On conflict, the
  ledger wins.
- The extractor is read-only. It does not fetch, register, send or confirm.
- A confirmation email received after a submission must not be clicked by a machine.
- One command touches one channel (R-520). Products are partitioned by
  `center_domain` to prevent cross-domain contamination.
- Chinese operator notes remain local data and never enter outbound copy (R-522).

## 5. Example

`assets/prose/TODO-example.md` is the offline fixture used by
`tests/test_prose.py`. It contains three ready candidates, one ambiguous row and one
excluded address.
