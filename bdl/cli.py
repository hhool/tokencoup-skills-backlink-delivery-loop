"""Command line entry point. Controllers only parse arguments and format output."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__
from .adapters.events import ACTIONS
from .config import Config
from .errors import ConfigError, DeliveryError
from .log import JsonLogger, new_batch_id
from .services import window
from .services.pipeline import build_manifest
from .templates import CHANNEL_TYPES, TemplateSet


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delivery",
        description="Prepare and record backlink outreach work. Never transmits.",
    )
    parser.add_argument("--batch-id", default="", help="Override the generated batch id")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Validate configuration and report policy state")

    validate = sub.add_parser("validate-snapshot", help="Validate an upstream snapshot")
    validate.add_argument("--file", required=True)
    validate.add_argument("--center-domain", default="")

    build = sub.add_parser("build-manifest", help="Build a review-only delivery manifest")
    build.add_argument("--snapshot", required=True)
    build.add_argument("--prose-dir", default="")
    build.add_argument("--history", default="", help="Primary history file (JSON or JSONL)")
    build.add_argument("--history-dir", default="", help="Directory of additional history files")
    build.add_argument("--out", default="")
    build.add_argument("--center-domain", default="")

    lookup = sub.add_parser("lookup", help="Reconcile recorded sends for one target domain")
    lookup.add_argument("--target-domain", required=True)
    lookup.add_argument("--history", default="", help="History file (JSON or JSONL)")
    lookup.add_argument("--history-dir", default="")

    render = sub.add_parser("render-template", help="Preview rendered outbound copy for a channel")
    render.add_argument("--channel-type", required=True, choices=list(CHANNEL_TYPES))
    render.add_argument("--templates-dir", default="", help="Override templates directory")
    render.add_argument("--context-json", default="", help="JSON object of template variables")

    scan = sub.add_parser("scan", help="Fetch target pages and observe live backlinks (L4, read-only)")
    scan.add_argument("--manifest", required=True, help="A delivery-manifest.json produced earlier")
    scan.add_argument("--out", default="", help="Directory for the observation ledger and proposals")
    scan.add_argument("--auto-confirm", action="store_true",
                      help="Mark found links live locally and queue confirm proposals")

    plan = sub.add_parser("plan-window", help="Compute the submitted-to-live observation schedule")
    plan.add_argument("--submitted-at", required=True)
    plan.add_argument("--max-checks", type=int, default=window.MAX_CHECKS)

    classify = sub.add_parser("classify", help="Classify one L4 observation (found/absent/timeout/robots)")
    classify.add_argument("--outcome", required=True, choices=["found", "absent", "timeout", "robots"])
    classify.add_argument("--http-status", type=int, required=True)

    package = sub.add_parser("package", help="Build a standalone distribution zip")
    package.add_argument("--out", default="", help="Output directory (default: ./dist)")

    export = sub.add_parser(
        "export-snapshot",
        help="Export a versioned contract-C snapshot from a prospector store file",
    )
    export.add_argument("--store", required=True, help="Path to a backlink-prospector store-<domain>.json")
    export.add_argument("--out", required=True, help="Write the snapshot document to this path")
    export.add_argument("--repo", default="tokencoup-skills-backlink-prospector",
                        help="Source repo label recorded in snapshot.source.repo")
    export.add_argument(
        "--enrichment",
        default="",
        help="Discovery overlay from backlink-prospector tools/discover_entry.py "
        "(adds submission_url and contact_email to the snapshot)",
    )

    imp = sub.add_parser(
        "import-prospects",
        help="Import a backlink-prospector CSV export as a contract-C snapshot",
    )
    imp.add_argument("--csv", required=True, help="Path to backlink-prospects-<domain>-<stamp>.csv")
    imp.add_argument("--out", default="", help="Write the snapshot here (omit with --dry-run)")
    imp.add_argument("--center-domain", default="", help="Defaults to BDL_CENTER_DOMAIN, then the file name")
    imp.add_argument("--repo", default="tokencoup-skills-backlink-prospector")
    imp.add_argument("--dry-run", action="store_true", help="Report the mapping without writing")

    observe = sub.add_parser(
        "observe",
        help="Fetch a target page and check for a live backlink (L4, read-only GET)",
    )
    observe.add_argument("--target-url", required=True, help="Third-party page to inspect")
    observe.add_argument("--own-url", required=True, help="Our URL expected to appear as a backlink")
    observe.add_argument("--record", default="", help="Append the observation as JSONL to this path")

    rec = sub.add_parser("record", help="Record one delivery action (append-only, idempotent)")
    rec.add_argument("--target-domain", required=True)
    rec.add_argument("--channel", required=True, choices=["form", "email"])
    rec.add_argument("--action", required=True, choices=list(ACTIONS))
    rec.add_argument("--at", default="", help="ISO-8601, defaults to now")
    rec.add_argument("--address", default="", help="Mailbox involved (required for email events)")
    rec.add_argument("--provider-id", default="", help="Required for a send")
    rec.add_argument("--evidence", default="", help="Screenshot path or thank-you URL (required for a submission)")
    rec.add_argument("--reply-class", default="", choices=["", "yes", "no", "pay", "reciprocal"])
    rec.add_argument("--note", default="")
    rec.add_argument("--actor", default="", help="Who performed the action")
    rec.add_argument("--center-domain", default="")
    rec.add_argument("--records-dir", default="")

    poll = sub.add_parser(
        "poll-receipts",
        help="Poll the sending provider for delivered / bounced events (L2, read-only)",
    )
    poll.add_argument("--records-dir", default="")
    poll.add_argument("--provider", default="", help="resend | postmark (defaults to BDL_PROVIDER)")
    poll.add_argument("--base-url", default="", help="Override the provider API base")
    poll.add_argument("--out", default="", help="Directory for the receipt ledger and proposals")
    poll.add_argument("--fixture", default="", help="Read statuses from a local file, not the network")
    poll.add_argument("--auto-confirm", action="store_true",
                      help="Write resolving receipts straight into the event log")
    poll.add_argument("--dry-run", action="store_true", help="Poll and report, write nothing")
    poll.add_argument("--window-days", type=int, default=0,
                      help="Only poll sends inside this window (default BDL_RECEIPT_WINDOW_DAYS)")
    poll.add_argument("--limit", type=int, default=0, help="Stop after N sends (0 = all)")
    poll.add_argument("--actor", default="provider-poll")
    poll.add_argument("--map-status", default="",
                      help="Print how one provider status word maps, e.g. --map-status bounced")

    draft = sub.add_parser(
        "draft",
        help="Draft sendable .eml files from a manifest (writes files, never sends)",
    )
    draft.add_argument("--manifest", required=True, help="delivery-manifest.json")
    draft.add_argument("--out", default="", help="Directory for the .eml files")
    draft.add_argument("--from", dest="from_addr", default="", help="From address (or BDL_FROM_EMAIL)")
    draft.add_argument("--from-name", default="", help="Display name for the From header")
    draft.add_argument("--ceiling", type=float, default=0.80,
                       help="Refuse a draft this similar to one already drafted (0-1)")
    draft.add_argument("--domain", default="", help="Draft only this target domain")
    draft.add_argument("--dry-run", action="store_true", help="Render and check, write nothing")

    replies = sub.add_parser(
        "replies",
        help="Read exported replies, match them to targets, and queue classification proposals",
    )
    replies.add_argument("--source", required=True,
                         help="An .eml file, an .mbox archive, or a directory of .eml exports")
    replies.add_argument("--manifest", default="", help="Manifest for Message-ID based matching")
    replies.add_argument("--records-dir", default="")
    replies.add_argument("--out", default="", help="Directory for the proposal queue")
    replies.add_argument("--auto-confirm", action="store_true",
                         help="Write yes/no at high confidence straight into the event log")
    replies.add_argument("--dry-run", action="store_true", help="Classify and report, write nothing")
    replies.add_argument("--actor", default="reply-parser")

    st = sub.add_parser("status", help="Current state per domain and channel, folded from the event log")
    st.add_argument("--records-dir", default="")
    st.add_argument("--observations-dir", default="")
    st.add_argument("--history", default="", help="Legacy send-history file to seed state from")
    st.add_argument("--manifest", default="", help="Seed untouched rows from a delivery manifest")
    st.add_argument("--channel", default="", choices=["", "form", "email"])
    st.add_argument("--target-domain", default="")
    st.add_argument("--center-domain", default="")
    st.add_argument("--limit", type=int, default=0, help="Truncate the row list (0 = all)")

    ui = sub.add_parser("serve", help="Run the local review UI (127.0.0.1 only)")
    ui.add_argument("--manifest", required=True, help="delivery-manifest.json (or its batch folder)")
    ui.add_argument("--drafts", default="data/drafts", help="Directory of .eml drafts")
    ui.add_argument("--records-dir", default="")
    ui.add_argument("--packs", default="", help="Directory of prefill packs to offer for download")
    ui.add_argument("--center-domain", default="")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=8765)

    forms = sub.add_parser("forms", help="Form fingerprint registry for submission forms")
    forms_sub = forms.add_subparsers(dest="forms_command", required=True)
    forms_sub.add_parser("list").add_argument("--domain", default="")
    forms_show = forms_sub.add_parser("show")
    forms_show.add_argument("--fingerprint", required=True)
    forms_register = forms_sub.add_parser("register")
    forms_register.add_argument("--domain", required=True)
    forms_register.add_argument("--method", default="POST")
    forms_register.add_argument("--action", required=True, help="Form action URL or path")
    forms_register.add_argument("--fields", required=True, help="Comma-separated field names as rendered")
    forms_register.add_argument("--map", action="append", default=[], help="label=value, repeatable")
    forms_register.add_argument("--success-selector", default="")
    forms_register.add_argument("--evidence", default="")
    forms_register.add_argument("--captcha", action="store_true")
    forms_register.add_argument("--requires-account", action="store_true")
    forms_register.add_argument("--reviewed-by", required=True, help="Named human who verified the form")
    forms_register.add_argument("--notes", default="")
    forms_check = forms_sub.add_parser("check")
    forms_check.add_argument("--method", default="POST")
    forms_check.add_argument("--action", required=True)
    forms_check.add_argument("--fields", required=True)
    forms_check.add_argument("--forms-dir", default="")
    forms_prefill = forms_sub.add_parser(
        "prefill",
        help="Compose per-form value packs for the browser userscript",
    )
    forms_prefill.add_argument("--manifest", required=True, help="delivery-manifest.json")
    forms_prefill.add_argument("--snapshot", default="",
                               help="Contract-C snapshot, for form_shape and form_fields")
    forms_prefill.add_argument("--out", default="", help="Directory for the pack")
    forms_prefill.add_argument("--from", dest="from_addr", default="", help="From address (or BDL_FROM_EMAIL)")
    forms_prefill.add_argument("--from-name", default="")
    forms_prefill.add_argument("--own-url", default="", help="Landing URL (or BDL_OWN_LANDING_URL)")
    forms_prefill.add_argument("--ceiling", type=float, default=0.80,
                               help="Refuse a pack this similar to one already composed (0-1)")
    forms_prefill.add_argument("--domain", default="", help="Pack only this target domain")
    forms_prefill.add_argument("--dry-run", action="store_true", help="Compose and report, write nothing")
    forms_adopt = forms_sub.add_parser(
        "adopt",
        help="Register a form from the receipt the userscript saved after a human submitted",
    )
    forms_adopt.add_argument("--file", required=True, help="Receipt JSON written by the userscript")
    forms_adopt.add_argument("--reviewed-by", required=True)
    forms_adopt.add_argument("--forms-dir", default="")
    forms_adopt.add_argument("--dry-run", action="store_true", help="Validate and report, write nothing")
    forms_lookup = forms_sub.add_parser("lookup", help="Resolve a form to an exact or reused field map")
    forms_lookup.add_argument("--method", default="POST")
    forms_lookup.add_argument("--action", required=True)
    forms_lookup.add_argument("--fields", required=True)
    forms_lookup.add_argument("--shape", default="")
    forms_lookup.add_argument("--forms-dir", default="")
    return parser


def _output(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _history_sources(args: argparse.Namespace, cfg: Config) -> list:
    """Build the ordered list of history sources for one run.

    Order: explicit ``--history`` file, the default per-run file under the out
    directory, then ``--history-dir`` and the configured ``BDL_HISTORY_DIR``.
    ``collect_history`` silently skips anything missing.
    """
    sources: list = []
    explicit = getattr(args, "history", "")
    if explicit:
        sources.append(explicit)
    default_file = cfg.out_dir / "send-history.json"
    sources.append(str(default_file))
    for directory in (getattr(args, "history_dir", ""), str(cfg.history_dir)):
        if directory:
            sources.append(directory)
    return sources


def _run(args: argparse.Namespace) -> dict:
    cfg = Config.from_env()
    logger = JsonLogger(args.batch_id or new_batch_id(), cfg.log_level)

    if args.command == "doctor":
        return {
            "ok": True,
            "version": __version__,
            "center_domain": cfg.center_domain,
            "own_site_base": cfg.own_site_base,
            "tier": cfg.tier,
            "auto_transmit": cfg.auto_transmit,
            "paths": {
                "home": str(cfg.home),
                "snapshots": str(cfg.snapshot_dir),
                "prose": str(cfg.prose_dir),
                "out": str(cfg.out_dir),
                "records": str(cfg.records_dir),
                "observations": str(cfg.observation_dir),
                "forms": str(cfg.forms_dir),
                "templates": str(cfg.templates_dir),
            },
            "windows": {
                "deferral_days": cfg.deferral_window_days,
                "receipt_days": cfg.receipt_window_days,
                "contact_lost_days": cfg.contact_lost_days,
                "daily_ceiling": cfg.daily_ceiling,
            },
            "auto_confirm": cfg.auto_confirm,
            "provider": {
                "name": cfg.provider or None,
                "base_url": cfg.provider_base_url or None,
                "api_key_configured": cfg.provider_key_present,
                "note": "Read-only: one GET per unresolved send, inside the receipt window.",
            },
            "policy": "Human review is required before any transmission (R-520 / R-530 tier B).",
        }

    if args.command == "validate-snapshot":
        from .adapters.snapshot import load_snapshot
        snapshot = load_snapshot(args.file, args.center_domain or cfg.center_domain)
        return {
            "ok": True,
            "center_domain": snapshot.center_domain,
            "schema_version": snapshot.schema_version,
            "prospects": len(snapshot.prospects),
            "file_hash": snapshot.file_hash,
        }

    if args.command == "build-manifest":
        center = args.center_domain or cfg.center_domain
        if not center:
            raise DeliveryError("A center domain is required", hint="--center-domain or BDL_CENTER_DOMAIN")
        effective = replace(cfg, center_domain=center)
        if not cfg.own_site_base:
            effective = replace(effective, own_site_base="https://" + center)
        history_sources = _history_sources(args, effective)
        manifest = build_manifest(
            cfg=effective,
            snapshot_path=args.snapshot,
            prose_dir=args.prose_dir or str(cfg.prose_dir),
            history_sources=history_sources,
            out_dir=args.out or str(cfg.out_dir),
            batch_id=args.batch_id or new_batch_id(),
            logger=logger,
        )
        return {"ok": True, "batch_id": manifest["batch_id"], "summary": manifest["summary"], "gaps": manifest["gaps"]}

    if args.command == "lookup":
        from .adapters.outbox import lookup_history

        sources = _history_sources(args, cfg)
        events = lookup_history(args.target_domain, sources)
        return {
            "ok": True,
            "target_domain": args.target_domain,
            "center_domain": cfg.center_domain,
            "events": events,
            "count": len(events),
            "note": "History is a derived, replayable record; absence is a gap, not a zero.",
        }

    if args.command == "render-template":
        directory = args.templates_dir or str(cfg.templates_dir)
        template_set = TemplateSet.load(directory, cfg.center_domain)
        context = json.loads(args.context_json) if args.context_json else {}
        rendered = template_set.render(args.channel_type, context)
        return {"ok": True, "channel_type": args.channel_type, "templates_dir": directory, "content": rendered}

    if args.command == "scan":
        from .models import ManifestRow
        from .services.observe import default_fetcher
        from .services.scan import scan_rows

        manifest_path = Path(args.manifest)
        if not manifest_path.is_file():
            raise DeliveryError("Manifest not found", path=str(manifest_path))
        manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows = [ManifestRow(**row) for row in manifest_doc.get("rows", [])]
        out_dir = Path(args.out or str(cfg.observation_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        auto_confirm = bool(args.auto_confirm) or cfg.auto_confirm
        result = scan_rows(
            rows=rows,
            fetcher=default_fetcher(),
            auto_confirm=auto_confirm,
            center_domain=manifest_doc.get("center_domain", cfg.center_domain),
            observation_path=str(out_dir / "observations.jsonl"),
            proposal_path=str(out_dir / "confirm-proposals.jsonl"),
        )
        logger.emit(
            "scan_completed",
            scanned=result["scanned"],
            found=result["found"],
            confirmed=result["confirmed"],
        )
        return {"ok": True, **{k: v for k, v in result.items() if k != "proposals"},
                "proposals_count": len(result["proposals"])}

    if args.command == "plan-window":
        schedule = window.plan(args.submitted_at, args.max_checks)
        return {
            "ok": True,
            "submitted_at": args.submitted_at,
            "schedule": schedule,
            "next_check_due": window.next_check_due(schedule, datetime.now(timezone.utc)),
            "terminal_state": window.terminal_state([]),
            "note": "Absence inside this window never declares failure (R-512 / R-513).",
        }

    if args.command == "package":
        from .package import build_package
        candidate = cfg.home if (cfg.home / "SKILL.md").is_file() else Path(__file__).resolve().parents[1]
        archive = build_package(candidate, args.out or None)
        return {"ok": True, "archive": str(archive), "bytes": archive.stat().st_size}

    if args.command == "classify":
        return {
            "ok": True,
            "outcome": window.classify(args.outcome, args.http_status),
            "note": "403, timeouts and robots refusals are unknown, not absent.",
        }

    if args.command == "export-snapshot":
        from .adapters.prospector_export import export_snapshot

        diagnostics: dict = {}
        snapshot = export_snapshot(
            args.store, args.repo, diagnostics=diagnostics, enrichment_path=args.enrichment
        )
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        with_channel = sum(
            1 for item in snapshot["prospects"]
            if item.get("submission_url") or item.get("contact_email")
        )
        return {
            "ok": True,
            "out": str(out),
            "center_domain": snapshot["center_domain"],
            "schema_version": snapshot["schema_version"],
            "prospects": len(snapshot["prospects"]),
            "enriched": diagnostics.get("enriched", 0),
            "with_channel": with_channel,
            "content_hash": snapshot["source"]["content_hash"],
        }

    if args.command == "import-prospects":
        from .adapters.csv_import import import_csv

        diagnostics: dict = {}
        snapshot = import_csv(
            args.csv,
            args.center_domain or cfg.center_domain,
            args.repo,
            diagnostics,
        )
        result = {
            "ok": True,
            "center_domain": snapshot["center_domain"],
            "prospects": len(snapshot["prospects"]),
            "content_hash": snapshot["source"]["content_hash"],
            "mapping": diagnostics,
        }
        if args.dry_run:
            result["dry_run"] = True
            return result
        if not args.out:
            raise DeliveryError("An output path is required", hint="--out or --dry-run")
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["out"] = str(out)
        return result

    if args.command == "observe":
        from .services.observe import default_fetcher, observe_target

        record = observe_target(default_fetcher(), args.target_url, args.own_url)
        if args.record:
            path = Path(args.record)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "observation": record}

    if args.command == "record":
        from .adapters.events import append_event, load_events
        from .services.record import build_event, policy_notice
        from .services.state import project

        center = args.center_domain or cfg.center_domain
        event = build_event(
            center_domain=center,
            target_domain=args.target_domain,
            channel=args.channel,
            action=args.action,
            at=args.at,
            address=args.address,
            provider_id=args.provider_id,
            evidence=args.evidence,
            reply_class=args.reply_class,
            note=args.note,
            actor=args.actor,
        )
        records_dir = Path(args.records_dir or str(cfg.records_dir))
        path = records_dir / ("events-%s.jsonl" % event["center_domain"])
        _, appended = append_event(path, event)
        state = project(
            load_events([path]),
            [],
            receipt_window_days=cfg.receipt_window_days,
            contact_lost_days=cfg.contact_lost_days,
        )
        current = [
            row for row in state["rows"]
            if row["target_domain"] == event["target_domain"] and row["channel"] == event["channel"]
        ]
        notice = policy_notice(event)
        if notice and appended:
            logger.emit("policy_refusal_recorded", target_domain=event["target_domain"],
                        reply_class=event.get("reply_class"))
        return {
            "ok": True,
            "appended": appended,
            "duplicate": not appended,
            "record_file": str(path),
            "event": event,
            "state": current[0] if current else None,
            "policy": notice or None,
            "note": "Append-only. A repeated event id is refused, not duplicated.",
        }

    if args.command == "poll-receipts":
        from .adapters.events import load_events
        from .adapters.provider_events import default_reader, fixture_reader, provider_config
        from .services.receipts import classify_one, execute_polls, pending_sends

        provider = args.provider or cfg.provider
        config = provider_config(provider, args.base_url or cfg.provider_base_url)
        if args.map_status:
            return {"ok": True, "provider": provider, **classify_one(provider, args.map_status)}

        records_dir = Path(args.records_dir or str(cfg.records_dir))
        out_dir = Path(args.out or str(cfg.observation_dir))
        moment = datetime.now(timezone.utc)
        events = load_events([records_dir])
        polls, already = pending_sends(
            events, moment, args.window_days or cfg.receipt_window_days, args.limit
        )
        # An empty ledger has nothing to ask a provider about, so it needs no key.
        if not polls:
            return {
                "ok": True,
                "provider": provider,
                "polls": 0,
                "already_resolved": already,
                "by_status": {"delivered": 0, "bounced": 0, "pending": 0, "sent": 0, "unknown": 0},
                "events_written": 0,
                "proposals": 0,
                "note": "No unresolved send inside the receipt window; nothing was queried.",
            }

        reader = fixture_reader(args.fixture) if args.fixture else default_reader(provider, config)
        result = execute_polls(
            events=events,
            polls=polls,
            already_resolved=already,
            reader=reader,
            provider=provider,
            config=config,
            now=moment,
            events_dir=records_dir,
            receipt_path=out_dir / "transport-receipts.jsonl",
            proposal_path=out_dir / "receipt-proposals.jsonl",
            auto_confirm=bool(args.auto_confirm) or cfg.auto_confirm,
            dry_run=bool(args.dry_run),
            actor=args.actor,
        )
        logger.emit(
            "receipts_polled",
            provider=provider,
            polls=result["polls"],
            delivered=result["by_status"].get("delivered", 0),
            bounced=result["by_status"].get("bounced", 0),
        )
        result["provider"] = provider
        result["note"] = (
            "A receipt proves transport (L2), never a live link (L4). An unreadable "
            "status stays unknown, never bounced."
        )
        return {"ok": True, **result}

    if args.command == "draft":
        from dataclasses import fields as dataclass_fields

        from .models import ManifestRow
        from .services.draft import draft_batch

        source = Path(args.manifest)
        if not source.is_file():
            raise ConfigError("Manifest not found", path=str(source))
        manifest = json.loads(source.read_text(encoding="utf-8"))
        known = {item.name for item in dataclass_fields(ManifestRow)}
        rows = [
            ManifestRow(**{key: value for key, value in item.items() if key in known})
            for item in manifest.get("rows", [])
            if isinstance(item, dict)
        ]
        if args.domain:
            rows = [row for row in rows if row.target_domain == args.domain]

        from_addr = args.from_addr or cfg.from_email
        if not from_addr:
            raise ConfigError(
                "A From address is required to draft mail",
                env="BDL_FROM_EMAIL",
                hint="The address a recipient would reply to",
            )
        center = manifest.get("center_domain") or cfg.center_domain
        report = draft_batch(
            rows,
            templates=TemplateSet.load(cfg.templates_dir, center),
            center_domain=center,
            own_site_base=cfg.own_site_base,
            from_addr=from_addr,
            out_dir=args.out or str(cfg.out_dir / "drafts"),
            from_name=args.from_name or cfg.from_name,
            ceiling=args.ceiling,
            write=not args.dry_run,
        )
        logger.emit(
            "drafts_built",
            drafted=report["counts"]["drafted"],
            duplicates_blocked=report["counts"]["duplicates_blocked"],
        )
        return {"ok": True, "dry_run": bool(args.dry_run), "from": from_addr, **report}

    if args.command == "replies":
        from .adapters.events import load_events
        from .adapters.mailbox import read_messages
        from .services.record import build_event
        from .services.replies import propose, PROPOSAL_KIND

        messages = read_messages(args.source)
        if not messages:
            raise ConfigError("No replies found to read", path=str(args.source),
                              hint="an .eml file, an .mbox archive, or a directory of .eml exports")

        records_dir = Path(args.records_dir or str(cfg.records_dir))
        events = load_events([records_dir])
        known = [str(event.get("target_domain") or "") for event in events]
        rows = []
        if args.manifest:
            from dataclasses import fields as dataclass_fields

            from .models import ManifestRow
            source = Path(args.manifest)
            if not source.is_file():
                raise ConfigError("Manifest not found", path=str(source))
            manifest = json.loads(source.read_text(encoding="utf-8"))
            fields_ = {item.name for item in dataclass_fields(ManifestRow)}
            rows = [
                ManifestRow(**{key: value for key, value in item.items() if key in fields_})
                for item in manifest.get("rows", [])
                if isinstance(item, dict)
            ]
            known.extend(str(row.target_domain) for row in rows)
        known = [item for item in dict.fromkeys(item for item in known if item)]

        proposal_path = ""
        if not args.dry_run and not args.auto_confirm:
            out_dir = Path(args.out or str(cfg.out_dir))
            proposal_path = out_dir / "reply-proposals.jsonl"

        report = propose(
            messages,
            known_domains=known,
            rows=rows,
            auto_confirm=args.auto_confirm,
            proposal_path=proposal_path,
        )

        written: List[str] = []
        if args.auto_confirm and not args.dry_run:
            for item in report["proposals"]:
                if item.get("reply_class") not in ("yes", "no") or item.get("confidence") != "high":
                    continue
                event = build_event(
                    center_domain=cfg.center_domain,
                    target_domain=str(item["target_domain"]),
                    channel="email",
                    action="reply",
                    address=str(item.get("address") or ""),
                    reply_class=item["reply_class"],
                    note="classified by reply-parser: %s" % ", ".join(item.get("signals") or []),
                    actor=args.actor,
                )
                path = records_dir / ("events-%s.jsonl" % (event["center_domain"] or "unknown"))
                from .adapters.events import append_event

                _, appended = append_event(path, event)
                if appended:
                    written.append(str(path))
            report["events_written"] = written
        logger.emit(
            "replies_classified",
            messages=report["messages"],
            matched=report["matched"],
            unmatched=len(report["unmatched"]),
        )
        return {"ok": True, "dry_run": bool(args.dry_run), **report}

    if args.command == "serve":
        from .server import serve

        url, server = serve(
            manifest_path=args.manifest,
            drafts_dir=args.drafts,
            records_dir=args.records_dir or cfg.records_dir,
            packs_dir=args.packs,
            center_domain=args.center_domain or cfg.center_domain,
            host=args.host,
            port=args.port,
        )
        logger.emit("review_server_listening", url=url, manifest=args.manifest)
        print(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return {"ok": True, "url": url}

    if args.command == "status":
        from .adapters.events import load_events, normalise_legacy
        from .adapters.observation import load_observations_dir
        from .services.state import merge_seeds, project, seed_rows_from_manifest

        center = args.center_domain or cfg.center_domain
        records_dir = Path(args.records_dir or str(cfg.records_dir))
        observations_dir = Path(args.observations_dir or str(cfg.observation_dir))

        events = load_events([records_dir])
        if args.history:
            events.extend(normalise_legacy(load_events([args.history])))
        observations = load_observations_dir(observations_dir)

        seeds: list = []
        if args.manifest:
            manifest_path = Path(args.manifest)
            if not manifest_path.is_file():
                raise DeliveryError("Manifest not found", path=str(manifest_path))
            seeds = seed_rows_from_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        if seeds:
            events = merge_seeds(events, seeds)

        result = project(
            events,
            observations,
            receipt_window_days=cfg.receipt_window_days,
            contact_lost_days=cfg.contact_lost_days,
            center_domain=center,
        )
        rows = result["rows"]
        if args.channel:
            rows = [row for row in rows if row["channel"] == args.channel]
        if args.target_domain:
            wanted = args.target_domain.strip().lower()
            rows = [row for row in rows if row["target_domain"] == wanted]
        total = len(rows)
        if args.limit > 0:
            rows = rows[: args.limit]
        result["rows"] = rows
        result["returned"] = len(rows)
        result["matched"] = total
        return {"ok": True, **result}

    if args.command == "forms":
        from .adapters import forms as registry

        directory = Path(getattr(args, "forms_dir", "") or str(cfg.forms_dir))
        if args.forms_command == "list":
            entries = registry.listing(directory, args.domain)
            return {"ok": True, "forms_dir": str(directory), "count": len(entries), "entries": entries}
        if args.forms_command == "show":
            entry = registry.show(directory, key=args.fingerprint)
            return {"ok": True, "forms_dir": str(directory), "entry": entry[0]}
        if args.forms_command == "register":
            names = [name.strip() for name in args.fields.split(",") if name.strip()]
            field_map = {}
            for pair in args.map or []:
                if "=" not in pair:
                    raise DeliveryError("Field map entries must be label=value", value=pair)
                label, value = pair.split("=", 1)
                field_map[label.strip()] = value.strip()
            entry = registry.register(
                directory,
                domain=args.domain,
                method=args.method,
                action=args.action,
                field_names=names,
                field_map=field_map,
                reviewed_by=args.reviewed_by,
                success_selector=args.success_selector,
                evidence=args.evidence,
                captcha=args.captcha,
                requires_account=args.requires_account,
                notes=args.notes,
            )
            return {
                "ok": True,
                "forms_dir": str(directory),
                "entry": entry,
                "note": "Registered. Next encounter prefills; a human still clicks submit.",
            }
        if args.forms_command == "lookup":
            names = [name.strip() for name in args.fields.split(",") if name.strip()]
            found = registry.lookup(
                directory, args.method, args.action, names, args.shape
            )
            return {
                "ok": True,
                "forms_dir": str(directory),
                **found,
                "note": (
                    "`exact` = this form was reviewed; `shape` = the same plugin on "
                    "another site, so the same wire field carries the same slot; "
                    "`none` = a human maps it once and `forms adopt` stores it."
                ),
            }
        if args.forms_command == "prefill":
            from dataclasses import fields as dataclass_fields

            from .models import ManifestRow
            from .services.prefill import build_packs

            source = Path(args.manifest)
            if not source.is_file():
                raise ConfigError("Manifest not found", path=str(source))
            manifest = json.loads(source.read_text(encoding="utf-8"))
            known = {item.name for item in dataclass_fields(ManifestRow)}
            rows = [
                ManifestRow(**{key: value for key, value in item.items() if key in known})
                for item in manifest.get("rows", [])
                if isinstance(item, dict)
            ]
            if args.domain:
                rows = [row for row in rows if row.target_domain == args.domain]

            prospects = []
            if args.snapshot:
                from .adapters.snapshot import load_snapshot
                snapshot = load_snapshot(args.snapshot, manifest.get("center_domain") or cfg.center_domain)
                prospects = snapshot.prospects

            from_addr = args.from_addr or cfg.from_email
            if not from_addr:
                raise ConfigError(
                    "A From address is required to fill a submission form",
                    env="BDL_FROM_EMAIL",
                    hint="The address the site would answer",
                )
            center = manifest.get("center_domain") or cfg.center_domain
            report = build_packs(
                rows,
                templates=TemplateSet.load(cfg.templates_dir, center),
                center_domain=center,
                own_landing_url=args.own_url or cfg.own_landing_url,
                from_email=from_addr,
                from_name=args.from_name or cfg.from_name,
                forms_dir=directory,
                prospects=prospects,
                ceiling=args.ceiling,
                out_dir=args.out or str(cfg.out_dir / "prefill"),
                write=not args.dry_run,
            )
            logger.emit("prefill_packs_built", packed=report["counts"]["packed"])
            return {"ok": True, "dry_run": bool(args.dry_run), "from": from_addr, **report}
        if args.forms_command == "adopt":
            from .services.prefill import load_receipt, record_command

            receipt = load_receipt(args.file)
            if args.dry_run:
                return {
                    "ok": True,
                    "dry_run": True,
                    "target_domain": receipt.get("target_domain"),
                    "fields": receipt.get("field_names"),
                    "slot_map": receipt.get("slot_map"),
                    "evidence": receipt.get("response_url"),
                    "would_register": True,
                }
            entry = registry.adopt_receipt(directory, receipt, args.reviewed_by)
            center = receipt.get("center_domain") or cfg.center_domain
            return {
                "ok": True,
                "forms_dir": str(directory),
                "entry": entry,
                "next_command": record_command(receipt, center, args.reviewed_by),
                "note": "The map is stored. Run next_command to record the submission event.",
            }
        names = [name.strip() for name in args.fields.split(",") if name.strip()]
        return {"ok": True, **registry.check(directory, args.method, args.action, names)}

    raise DeliveryError("Unknown command", command=getattr(args, "command", ""))


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = _parser().parse_args(argv)
        _output(_run(args))
        return 0
    except DeliveryError as exc:
        sys.stderr.write(json.dumps(exc.to_dict(), ensure_ascii=False) + "\n")
        return exc.exit_code
    except (ValueError, OSError) as exc:
        fallback = DeliveryError("Invalid input", kind=type(exc).__name__, reason=str(exc))
        sys.stderr.write(json.dumps(fallback.to_dict(), ensure_ascii=False) + "\n")
        return 1
    except Exception as exc:  # no traceback is ever exposed
        fallback = DeliveryError("Unexpected failure", kind=type(exc).__name__)
        sys.stderr.write(json.dumps(fallback.to_dict(), ensure_ascii=False) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
