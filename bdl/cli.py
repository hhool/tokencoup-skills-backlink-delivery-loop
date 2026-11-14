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
from .errors import DeliveryError
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

    st = sub.add_parser("status", help="Current state per domain and channel, folded from the event log")
    st.add_argument("--records-dir", default="")
    st.add_argument("--observations-dir", default="")
    st.add_argument("--history", default="", help="Legacy send-history file to seed state from")
    st.add_argument("--manifest", default="", help="Seed untouched rows from a delivery manifest")
    st.add_argument("--channel", default="", choices=["", "form", "email"])
    st.add_argument("--target-domain", default="")
    st.add_argument("--center-domain", default="")
    st.add_argument("--limit", type=int, default=0, help="Truncate the row list (0 = all)")

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

        snapshot = export_snapshot(args.store, args.repo)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return {
            "ok": True,
            "out": str(out),
            "center_domain": snapshot["center_domain"],
            "prospects": len(snapshot["prospects"]),
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
