"""Command line entry point. Controllers only parse arguments and format output."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from typing import List, Optional

from . import __version__
from .config import Config
from .errors import DeliveryError
from .log import JsonLogger, new_batch_id
from .services import window
from .services.pipeline import build_manifest


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
    build.add_argument("--history", default="")
    build.add_argument("--out", default="")
    build.add_argument("--center-domain", default="")

    plan = sub.add_parser("plan-window", help="Compute the submitted-to-live observation schedule")
    plan.add_argument("--submitted-at", required=True)
    plan.add_argument("--max-checks", type=int, default=window.MAX_CHECKS)

    classify = sub.add_parser("classify", help="Classify one observation")
    classify.add_argument("--outcome", required=True, choices=["found", "absent", "timeout", "robots"])
    classify.add_argument("--http-status", type=int, required=True)

    package = sub.add_parser("package", help="Build a standalone distribution zip")
    package.add_argument("--out", default="", help="Output directory (default: ./dist)")
    return parser


def _output(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


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
        manifest = build_manifest(
            cfg=effective,
            snapshot_path=args.snapshot,
            prose_dir=args.prose_dir or str(cfg.prose_dir),
            history_path=args.history or str(cfg.out_dir / "send-history.json"),
            out_dir=args.out or str(cfg.out_dir),
            batch_id=args.batch_id or new_batch_id(),
            logger=logger,
        )
        return {"ok": True, "batch_id": manifest["batch_id"], "summary": manifest["summary"], "gaps": manifest["gaps"]}

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
        from pathlib import Path

        from .package import build_package
        candidate = cfg.home if (cfg.home / "SKILL.md").is_file() else Path(__file__).resolve().parents[1]
        archive = build_package(candidate, args.out or None)
        return {"ok": True, "archive": str(archive), "bytes": archive.stat().st_size}

    return {
        "ok": True,
        "outcome": window.classify(args.outcome, args.http_status),
        "note": "403, timeouts and robots refusals are unknown, not absent.",
    }


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
