"""Local review server: the human gate of the delivery loop, in a browser.

Everything here is read-only except two endpoints. POST /record does exactly
what `bdl record` does on the command line - build one validated event and
append it to the log. POST /import does exactly what `import-prospects` +
`build-manifest` + `draft` do - turn an uploaded prospector CSV into drafts.
Both are operator actions a human clicked, never an automation. The server
binds 127.0.0.1 only and never sends mail, never submits a form and never
marks a link live: those stay with the provider poll, the form flow and the
L4 observer.
"""

from __future__ import annotations

import html
import email as email_lib
import email.policy as email_policy
import json
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from email import message_from_string
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .adapters.csv_import import import_csv
from .adapters.events import append_event, load_events
from .errors import DeliveryError
from .log import JsonLogger, new_batch_id
from .models import ManifestRow, normalize_domain
from .services.pipeline import build_manifest
from .services.record import build_event
from .services.state import project
from .templates import TemplateSet

STATUS_ORDER = (
    "ready_to_submit", "ready_for_review", "needs_enrichment",
    "no_channel", "blocked", "deferred",
)

STATUS_COLORS = {
    "ready_to_submit": "#1a7f37",
    "ready_for_review": "#1a7f37",
    "needs_enrichment": "#9a6700",
    "no_channel": "#57606a",
    "blocked": "#cf222e",
    "deferred": "#57606a",
}

# The actions the review UI offers. sent / bounced / delivered / reply arrive
# through the provider poll and the reply parser; a human records those there.
UI_ACTIONS = {
    "email": ("queued", "sent", "closed"),
    "form": ("queued", "submitted", "closed"),
}

_ACTION_LABEL = {
    "queued": "Approve (queued)",
    "sent": "I sent it",
    "submitted": "I submitted the form",
    "closed": "Close domain",
}


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _read_manifest(path: str | Path) -> Dict[str, Any]:
    target = Path(path)
    if target.is_dir():
        nested = target / "delivery-manifest.json"
        target = nested if nested.is_file() else target
    return json.loads(target.read_text(encoding="utf-8"))


def _draft_info(drafts_dir: str | Path, domain: str) -> Optional[Dict[str, str]]:
    path = Path(drafts_dir) / ("%s.eml" % domain)
    if not path.is_file():
        return None
    message = message_from_string(path.read_text(encoding="utf-8", errors="replace"))
    body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                body = str(part.get_payload(decode=True) or b"", "utf-8", "replace")
                break
    else:
        body = str(message.get_payload(decode=True) or b"", "utf-8", "replace")
    return {
        "to": str(message.get("To") or ""),
        "subject": str(message.get("Subject") or ""),
        "provider_id": str(message.get("X-BDL-Provider-Id") or ""),
        "submission_url": str(message.get("X-BDL-Submission-URL") or ""),
        "discovered_on": str(message.get("X-BDL-Discovered-On") or ""),
        "body": body.strip(),
        "path": str(path),
    }


def _packs_index(packs_dir: str | Path) -> List[Dict[str, str]]:
    folder = Path(packs_dir)
    if not folder.is_dir():
        return []
    packs = []
    for path in sorted(folder.glob("prefill-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        packs.append({
            "name": path.name,
            "generated_at": str(data.get("generated_at") or ""),
            "targets": str(len(data.get("targets") or [])),
        })
    return packs


class ReviewState:
    """Everything the handler needs; mutable, so /import can swap the batch."""

    def __init__(
        self,
        manifest_path: str,
        drafts_dir: str,
        records_dir: str,
        packs_dir: str = "",
        center_domain: str = "",
        prose_dir: str = "",
        cfg: Any = None,
        workdir: str = "",
    ) -> None:
        self.manifest_path = str(manifest_path)
        self.drafts_dir = str(drafts_dir)
        self.records_dir = str(records_dir)
        self.packs_dir = str(packs_dir)
        self.prose_dir = str(prose_dir)
        self.cfg = cfg
        self.workdir = str(workdir) or str(Path(records_dir).parent / "workdir")
        self.manifest = _read_manifest(manifest_path)
        self.center = normalize_domain(
            center_domain or self.manifest.get("center_domain") or ""
        )

    def switch(self, manifest_path: str, drafts_dir: str) -> None:
        """Point the live dashboard at a freshly imported batch."""
        self.manifest_path = str(manifest_path)
        self.drafts_dir = str(drafts_dir)
        self.manifest = _read_manifest(manifest_path)
        center = normalize_domain(str(self.manifest.get("center_domain") or ""))
        if center:
            self.center = center

    # -- projection ---------------------------------------------------------

    def rows(self) -> List[Dict[str, Any]]:
        rows = []
        projected = self.projected()
        for row in self.manifest.get("rows", []):
            domain = normalize_domain(row.get("target_domain") or "")
            entry = dict(row)
            entry["target_domain"] = domain
            entry["draft"] = _draft_info(self.drafts_dir, domain)
            entry["recorded"] = {
                key: value for key, value in projected.items()
                if key[0] == domain
            }
            rows.append(entry)
        return rows

    def projected(self) -> Dict[Tuple[str, str], Dict[str, Any]]:
        events = load_events([self.records_dir]) if Path(self.records_dir).is_dir() else []
        folded = project(events, center_domain=self.center)
        keyed = {}
        for row in folded.get("rows", []):
            keyed[(str(row.get("target_domain") or ""), str(row.get("channel") or ""))] = row
        return keyed

    def counts(self) -> Dict[str, int]:
        tally: Dict[str, int] = {}
        for row in self.manifest.get("rows", []):
            status = str(row.get("status") or "?")
            tally[status] = tally.get(status, 0) + 1
        return tally

    # -- mutation: the only write path --------------------------------------

    def record(self, form: Dict[str, str]) -> Dict[str, Any]:
        event = build_event(
            center_domain=self.center,
            target_domain=form.get("target_domain", ""),
            channel=form.get("channel", ""),
            action=form.get("action", ""),
            address=form.get("address", ""),
            provider_id=form.get("provider_id", ""),
            evidence=form.get("evidence", ""),
            note=form.get("note", ""),
            actor=form.get("actor", "review-ui"),
        )
        from pathlib import Path as _Path

        records = _Path(self.records_dir)
        records.mkdir(parents=True, exist_ok=True)
        path = records / ("events-%s.jsonl" % (event["center_domain"] or "unknown"))
        _, appended = append_event(path, event)
        return {"event": event, "appended": appended, "path": str(path)}

    # -- the CSV upload chain ----------------------------------------------

    def import_and_build(
        self, filename: str, csv_bytes: bytes, center_domain: str = ""
    ) -> Dict[str, Any]:
        """The upload chain: import CSV -> snapshot -> manifest -> drafts.

        Each stage is the same adapter/service the CLI uses. Nothing here
        transmits; the drafts land in the dashboard awaiting a human.
        """
        if not csv_bytes:
            raise DeliveryError("The upload was empty", hint="choose a prospect CSV file")
        workdir = Path(self.workdir)
        stamp = new_batch_id()
        uploads = workdir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename or "prospects.csv").name or "prospects.csv"
        csv_path = uploads / ("%s-%s" % (stamp, safe_name))
        csv_path.write_bytes(csv_bytes)

        diagnostics: Dict[str, Any] = {}
        snapshot = import_csv(
            str(csv_path), (center_domain or self.center or "").strip(), diagnostics=diagnostics,
        )
        center = str(snapshot["center_domain"])
        snapshots = workdir / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        snap_path = snapshots / ("%s.snapshot.json" % stamp)
        snap_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.cfg is None:
            raise DeliveryError(
                "The server was started without a config; the import chain needs one",
                hint="start with `delivery serve`, which loads BDL_* env and .env",
            )
        cfg = replace(self.cfg, center_domain=center)
        if not cfg.own_site_base:
            cfg = replace(cfg, own_site_base="https://" + center)

        batch_id = new_batch_id()
        out_dir = workdir / "out" / batch_id
        manifest = build_manifest(
            cfg=cfg,
            snapshot_path=str(snap_path),
            prose_dir=self.prose_dir,
            history_sources=[],
            out_dir=str(out_dir),
            batch_id=batch_id,
            logger=JsonLogger(batch_id),
        )
        manifest_path = out_dir / "delivery-manifest.json"

        drafted = 0
        skipped = 0
        draft_note = ""
        if self.cfg.from_email:
            from .services.draft import draft_batch

            rows = [
                ManifestRow(**{key: value for key, value in item.items() if key in {
                    field.name for field in dataclass_fields(ManifestRow)}})
                for item in manifest.get("rows", []) if isinstance(item, dict)
            ]
            report = draft_batch(
                rows,
                templates=TemplateSet.load(cfg.templates_dir, center),
                center_domain=center,
                own_site_base=cfg.own_site_base,
                from_addr=self.cfg.from_email,
                out_dir=str(workdir / "drafts"),
                from_name=self.cfg.from_name,
            )
            drafted = int(report["counts"]["drafted"])
            skipped = len(report.get("skips") or [])
        else:
            draft_note = "no BDL_FROM_EMAIL set, so drafts were not written; the manifest is ready"

        self.switch(manifest_path, workdir / "drafts")
        return {
            "batch_id": batch_id,
            "center_domain": center,
            "prospects": diagnostics.get("prospects"),
            "collapsed": diagnostics.get("collapsed_duplicates"),
            "manifest": manifest.get("summary", {}),
            "drafted": drafted,
            "skipped": skipped,
            "draft_note": draft_note,
            "manifest_path": str(manifest_path),
        }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _page(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>%s</title><style>"
        "body{font:14px/1.5 -apple-system,'Segoe UI',sans-serif;color:#1f2328;"
        "background:#f6f8fa;margin:0;padding:24px}"
        "main{max-width:1080px;margin:0 auto;background:#fff;border:1px solid #d0d7de;"
        "border-radius:10px;padding:20px 24px}"
        "table{border-collapse:collapse;width:100%%}"
        "th,td{border-bottom:1px solid #eaeef2;padding:8px 10px;text-align:left;"
        "vertical-align:top}"
        "th{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#57606a}"
        "td form{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}"
        "input,select,button{font:13px -apple-system,'Segoe UI',sans-serif;padding:5px 8px;"
        "border:1px solid #d0d7de;border-radius:6px}"
        "button{background:#1f6feb;color:#fff;border-color:#1f6feb;cursor:pointer}"
        "button.ghost{background:#f6f8fa;color:#1f2328;border-color:#d0d7de}"
        ".badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;"
        "font-weight:600;color:#fff;background:#57606a}"
        ".flash{padding:10px 14px;border-radius:8px;margin-bottom:16px;background:#dafbe1;"
        "color:#1a7f37;border:1px solid #a4e8b7}"
        ".error{padding:10px 14px;border-radius:8px;margin-bottom:16px;background:#ffebe9;"
        "color:#cf222e;border:1px solid #ffc1bc}"
        ".muted{color:#57606a;font-size:12px}"
        "pre{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;"
        "padding:12px;white-space:pre-wrap;font:12px/1.6 ui-monospace,Menlo,monospace}"
        ".details summary{cursor:pointer}"
        "</style></head><body><main>%s</main></body></html>" % (_esc(title), body)
    )


def _record_form(
    row: Dict[str, Any], channel: str, address: str, action: str
) -> str:
    needs = {"sent": ("provider_id", "provider id from your sending provider"),
             "submitted": ("evidence", "thank-you URL or screenshot path")}
    fields = []
    label = _ACTION_LABEL.get(action, action)
    if action == "sent":
        fields.append(
            "<input name='provider_id' placeholder='provider id' size='14' required>"
        )
    if action == "submitted":
        fields.append(
            "<input name='evidence' placeholder='thank-you URL / evidence' size='22' required>"
        )
    fields.append("<input name='note' placeholder='note (optional)' size='14'>")
    fields.append("<input name='actor' value='operator' size='8' title='who did this'>")
    fields.append("<input type='hidden' name='target_domain' value='%s'>" % _esc(row.get("target_domain")))
    fields.append("<input type='hidden' name='channel' value='%s'>" % _esc(channel))
    fields.append("<input type='hidden' name='action' value='%s'>" % _esc(action))
    if address:
        fields.append("<input type='hidden' name='address' value='%s'>" % _esc(address))
    return (
        "<form method='post' action='/record'>%s"
        "<button type='submit'>%s</button></form>"
        % ("".join(fields), _esc(label))
    )


def render_dashboard(state: ReviewState, flash: str = "", error: str = "") -> str:
    parts: List[str] = [
        "<h1>Delivery review - %s</h1>" % _esc(state.center),
        "<p><a href='/import'>Import a prospect CSV &rarr;</a></p>",
    ]
    if flash:
        parts.append("<div class='flash'>%s</div>" % _esc(flash))
    if error:
        parts.append("<div class='error'>%s</div>" % _esc(error))
    tally = state.counts()
    chips = " ".join(
        "<span class='badge' style='background:%s'>%s: %d</span>"
        % (STATUS_COLORS.get(status, "#57606a"), _esc(status), count)
        for status, count in sorted(tally.items())
    )
    packs = _packs_index(state.packs_dir)
    pack_html = ""
    if packs:
        newest = packs[0]
        pack_html = (
            "<p class='muted'>prefill pack: <a href='/packs/%s'>%s</a> "
            "(%s target(s), %s)</p>"
            % (_esc(newest["name"]), _esc(newest["name"]),
               _esc(newest["targets"]), _esc(newest["generated_at"]))
        )
    parts.append(
        "<p>%s</p>%s<p class='muted'>Records: %s. Everything a button writes "
        "goes through <code>bdl record</code> validation - nothing is sent "
        "from this page.</p>"
        % (chips, pack_html, _esc(state.records_dir))
    )

    parts.append("<table><tr><th>Target</th><th>Status</th><th>Channel</th>"
                 "<th>Draft</th><th>Recorded</th><th>Actions</th></tr>")
    for row in state.rows():
        status = str(row.get("status") or "")
        color = STATUS_COLORS.get(status, "#57606a")
        domain = str(row.get("target_domain") or "")
        channels = [str(c) for c in (row.get("channels") or [])]
        draft = row.get("draft")
        submission = str(row.get("submission_url") or "")

        # Draft cell
        if draft:
            draft_cell = (
                "<a href='/drafts/%s'>%s</a><br><span class='muted'>to %s</span>"
                % (_esc(domain), _esc(draft["subject"]),
                   _esc(draft["to"][:40] or "?"))
            )
        else:
            draft_cell = "<span class='muted'>-</span>"

        # Channel cell: form targets link to the form page, email targets to
        # the page the address was read from - the operator should never have
        # to re-derive where a channel came from.
        channel_bits = [_esc(row.get("channel_type") or "")] + [
            _esc(c) for c in channels
        ]
        if submission:
            channel_bits.append(
                "<a href='%s' target='_blank' rel='noreferrer'>form page</a>"
                % _esc(submission)
            )
        discovered = str(row.get("contact_source_url") or "")
        if not discovered and draft:
            discovered = draft.get("discovered_on") or ""
        if discovered:
            channel_bits.append(
                "<a href='%s' target='_blank' rel='noreferrer'>found on page</a>"
                % _esc(discovered)
            )
        channel_cell = "<br>".join(channel_bits)

        # Recorded cell
        recorded_bits = []
        for (target, channel), folded in sorted(row["recorded"].items()):
            recorded_bits.append(
                "%s: <strong>%s</strong>" % (_esc(channel or "?"), _esc(folded.get("state") or "?"))
            )
        recorded_cell = "<br>".join(recorded_bits) or "<span class='muted'>nothing yet</span>"

        # Action forms
        actions: List[str] = []
        for channel in channels:
            for action in UI_ACTIONS.get(channel, ("closed",)):
                if action == "sent" and not draft:
                    continue
                if action == "submitted" and not submission:
                    continue
                address = (draft or {}).get("to") or str(row.get("contact_email") or "")
                actions.append(_record_form(row, channel, address, action))
        if not actions:
            actions.append("<span class='muted'>-</span>")

        parts.append(
            "<tr><td><strong>%s</strong></td>"
            "<td><span class='badge' style='background:%s'>%s</span></td>"
            "<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (_esc(domain), color, _esc(status), channel_cell,
               draft_cell, recorded_cell, "".join(actions))
        )
    parts.append("</table>")

    parts.append(
        "<p class='muted'>Send a queued mail from your own mail client, then "
        "record it with <em>I sent it</em> plus the provider id. For forms, "
        "run the prefill pack in the browser, submit by hand, then record "
        "<em>I submitted the form</em> with the thank-you URL. A link is "
        "only <code>live</code> after the L4 observer finds it.</p>"
    )
    return _page("Delivery review", "".join(parts))


def render_import_page(state: ReviewState) -> str:
    prose = _esc(state.prose_dir or "(none)")
    from_email = _esc(getattr(state.cfg, "from_email", "") or "(not set)")
    body = [
        "<p><a href='/'>&larr; back to review</a></p>",
        "<h1>Import a prospect CSV</h1>",
        "<p class='muted'>The chain a run of the CLI would do, in one click: "
        "<code>import-prospects</code> &rarr; <code>build-manifest</code> &rarr; "
        "<code>draft</code>. Nothing is sent; drafts land on the dashboard "
        "awaiting your review.</p>",
        "<form method='post' action='/import' enctype='multipart/form-data'>",
        "<p><input type='file' name='csv' accept='.csv' required></p>",
        "<p><label>centre domain "
        "<input name='center_domain' value='%s' size='30'></label> "
        "<span class='muted'>blank = %s or the file name</span></p>"
        % (_esc(state.center), _esc(state.center or "guessed")),
        "<p><button type='submit'>Import &amp; build</button></p>",
        "</form>",
        "<h2>What the manifest will use</h2>",
        "<ul><li class='muted'>prose contacts: %s</li>"
        "<li class='muted'>from address: %s</li></ul>" % (prose, from_email),
        "<p class='muted'>Column contract: <code>domain</code> required; also read: "
        "<code>url, type_key, priority, http_status, valid, already_have, "
        "is_competitor, is_gap, note, note_en, submission_url, contact_email, "
        "contact_source, contact_source_url</code>. Other columns are reported "
        "as dropped.</p>",
    ]
    return _page("Import CSV", "".join(body))


def render_draft(state: ReviewState, domain: str) -> str:
    draft = _draft_info(state.drafts_dir, domain)
    if not draft:
        return _page("Not found", "<h1>No draft for %s</h1>" % _esc(domain))
    body = [
        "<p><a href='/'>&larr; back to review</a> &middot; "
        "<a href='/drafts/%s/raw'>download .eml</a></p>" % _esc(domain),
        "<h1>%s</h1>" % _esc(draft["subject"]),
        "<p class='muted'>To: %s</p>" % _esc(draft["to"]),
    ]
    if draft.get("discovered_on"):
        body.append(
            "<p class='muted'>Address found on: "
            "<a href='%s' target='_blank' rel='noreferrer'>%s</a></p>"
            % (_esc(draft["discovered_on"]), _esc(draft["discovered_on"]))
        )
    if draft.get("submission_url"):
        body.append(
            "<p class='muted'>Form page on the same site: "
            "<a href='%s' target='_blank' rel='noreferrer'>%s</a></p>"
            % (_esc(draft["submission_url"]), _esc(draft["submission_url"]))
        )
    body.append("<pre>%s</pre>" % _esc(draft["body"]))
    return _page("Draft - %s" % domain, "".join(body))


def render_error(state: ReviewState, message: str, form: Dict[str, str]) -> str:
    body = [
        "<h1>The record was refused</h1>",
        "<div class='error'>%s</div>" % _esc(message),
        "<p class='muted'>Nothing was written. The attempted row:</p>",
        "<pre>%s</pre>" % _esc(json.dumps(form, indent=2, sort_keys=True)),
        "<p><a href='/'>&larr; back to review</a></p>",
    ]
    return _page("Refused", "".join(body))


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

class ReviewHandler(BaseHTTPRequestHandler):
    state: ReviewState  # injected by serve()

    def log_message(self, fmt: str, *args: Any) -> None:
        pass  # keep the run log quiet; errors still surface via stderr

    def _send_html(self, markup: str, status: int = 200) -> None:
        payload = markup.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, query: Dict[str, str]) -> None:
        self.send_response(303)
        self.send_header("Location", "/?" + urlencode(query))
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self) -> Dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}

    def _multipart(self) -> Tuple[Dict[str, str], Dict[str, Tuple[str, bytes]]]:
        """Parse multipart/form-data with the stdlib email parser."""
        ctype = self.headers.get("Content-Type") or ""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        # policy=default gives EmailMessage with iter_parts(); the compat32
        # default policy would hand back a plain Message without it.
        message = email_lib.message_from_bytes(
            b"Content-Type: " + ctype.encode("utf-8")
            + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
            policy=email_policy.default,
        )
        fields: Dict[str, str] = {}
        files: Dict[str, Tuple[str, bytes]] = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition") or ""
            filename = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if filename is not None:
                files[name] = (filename, payload)
            else:
                fields[name] = payload.decode("utf-8", "replace")
        return fields, files

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        path = parts.path
        if path in ("/", ""):
            self._send_html(render_dashboard(
                self.state, flash=query.get("flash", ""), error=query.get("error", ""),
            ))
            return
        if path == "/import":
            self._send_html(render_import_page(self.state))
            return
        if path == "/favicon.ico":
            self._send_html("", 204)
            return
        if path.startswith("/drafts/") and path.endswith("/raw"):
            domain = normalize_domain(path[len("/drafts/"):-len("/raw")])
            source = Path(self.state.drafts_dir) / ("%s.eml" % domain)
            if source.is_file():
                payload = source.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "message/rfc822")
                self.send_header("Content-Disposition",
                                 "attachment; filename='%s.eml'" % domain)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self._send_html(_page("Not found", "<h1>No draft for %s</h1>" % _esc(domain)), 404)
            return
        if path.startswith("/drafts/"):
            domain = normalize_domain(path[len("/drafts/"):])
            if not domain:
                self._send_html(_page("Bad request", "<h1>A domain is required</h1>"), 400)
                return
            self._send_html(render_draft(self.state, domain))
            return
        if path.startswith("/packs/"):
            name = Path(path[len("/packs/"):]).name
            source = Path(self.state.packs_dir) / name
            if source.is_file():
                payload = source.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition",
                                 "attachment; filename='%s'" % name)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self._send_html(_page("Not found", "<h1>No pack</h1>"), 404)
            return
        self._send_html(_page("Not found", "<h1>404</h1>"), 404)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/import":
            fields, files = self._multipart()
            try:
                if "csv" not in files:
                    raise DeliveryError(
                        "No CSV file in the upload", hint="the file field must be named 'csv'",
                    )
                filename, payload = files["csv"]
                report = self.state.import_and_build(
                    filename, payload, fields.get("center_domain", ""),
                )
                summary = report["manifest"].get("by_status") or report["manifest"]
                flash = (
                    "Imported %(prospects)s prospect(s) for %(center)s; manifest: "
                    "%(ready)s ready / %(blocked)s blocked / %(no_channel)s no-channel"
                    % {"prospects": report.get("prospects"), "center": report["center_domain"],
                       "ready": summary.get("ready_to_submit", 0),
                       "blocked": summary.get("blocked", 0),
                       "no_channel": summary.get("no_channel", 0)}
                )
                if report.get("drafted"):
                    flash += "; %s draft(s) written, review before sending" % report["drafted"]
                elif report.get("draft_note"):
                    flash += "; " + report["draft_note"]
                self._redirect({"flash": flash})
            except DeliveryError as exc:
                detail = exc.to_dict()
                self._redirect({"error": json.dumps(detail, ensure_ascii=False)})
            except Exception as exc:  # pragma: no cover - defensive
                self._redirect({"error": "%s: %s" % (type(exc).__name__, exc)})
            return
        if path != "/record":
            self._send_html(_page("Not found", "<h1>404</h1>"), 404)
            return
        form = self._form()
        try:
            result = self.state.record(form)
            label = result["event"].get("action", "")
            where = result["path"]
            note = "recorded (already present)" if not result["appended"] else "recorded"
            self._redirect({
                "flash": "%s %s -> %s" % (label, form.get("target_domain", ""), note),
            })
        except DeliveryError as exc:
            detail = exc.to_dict()
            self._redirect({"error": json.dumps(detail, ensure_ascii=False)})
        except Exception as exc:  # pragma: no cover - defensive
            self._redirect({"error": "%s: %s" % (type(exc).__name__, exc)})


def serve(
    manifest_path: str,
    drafts_dir: str,
    records_dir: str,
    packs_dir: str = "",
    center_domain: str = "",
    prose_dir: str = "",
    cfg: Any = None,
    workdir: str = "",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> Tuple[str, Any]:
    """Start the review server. Returns (url, server). The caller runs serve_forever()."""
    state = ReviewState(
        manifest_path, drafts_dir, records_dir, packs_dir, center_domain,
        prose_dir=prose_dir, cfg=cfg, workdir=workdir,
    )
    handler = type("BoundReviewHandler", (ReviewHandler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    url = "http://%s:%d/" % (host, server.server_address[1])
    return url, server
