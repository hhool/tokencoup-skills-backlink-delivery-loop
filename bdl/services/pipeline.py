"""Orchestration: read -> normalise -> budget -> write local artefacts.

No network call, no form submission and no mail transmission happens here.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .. import CONTRACTS_VERSION, __version__
from ..adapters.outbox import append_receipt, collect_history, write_manifest
from ..adapters.prose import load_prose_contacts
from ..adapters.snapshot import load_snapshot
from ..config import Config
from ..log import JsonLogger
from ..models import as_dict, normalize_domain
from ..templates import TemplateSet
from .normalize import build_rows


def build_manifest(
    cfg: Config,
    snapshot_path: str,
    prose_dir: str,
    history_sources: list,
    out_dir: str,
    batch_id: str,
    logger: JsonLogger,
    now: Optional[datetime] = None,
) -> dict:
    moment = now or datetime.now(timezone.utc)
    snapshot = load_snapshot(snapshot_path, cfg.center_domain)
    contacts = load_prose_contacts(prose_dir)
    history = collect_history(history_sources)
    live_domains = {
        normalize_domain(str(row.get("target_domain", "")))
        for row in history
        if row.get("state") in {"live", "indexed"}
    }
    templates = TemplateSet.load(cfg.templates_dir, cfg.center_domain)
    rows = build_rows(
        snapshot,
        contacts,
        cfg.own_site_base,
        live_domains,
        history,
        moment,
        deferral_days=cfg.deferral_window_days,
        templates=templates,
        own_landing_url=cfg.own_landing_url,
    )
    counts = Counter(row.status for row in rows)

    gaps: list = []
    if not contacts:
        gaps.append("prose: no contact candidates extracted; fallback=no delivery channel")
    if not history:
        gaps.append("history: no send history supplied; fallback=empty window")

    manifest = {
        "center_domain": snapshot.center_domain,
        "batch_id": batch_id,
        "generated_at": moment.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "schema_version": CONTRACTS_VERSION,
        "policy": {
            "tier": cfg.tier,
            "auto_transmit": False,
            "auto_confirm": cfg.auto_confirm,
            "human_review_required": True,
            "deferral_window_days": cfg.deferral_window_days,
            "daily_ceiling": cfg.daily_ceiling,
            "rule_refs": ["R-510", "R-512", "R-520", "R-522", "R-530"],
            "note": "Manifest is a review artefact. No message was sent and no form was submitted.",
        },
        "summary": {
            "total": len(rows),
            "by_status": dict(sorted(counts.items())),
            "prose_contacts": len(contacts),
            "history_events": len(history),
            "snapshot_file_hash": snapshot.file_hash,
            "tool_version": __version__,
        },
        "rows": [as_dict(row) for row in rows],
        "gaps": gaps,
    }

    destination = Path(out_dir)
    write_manifest(destination / "delivery-manifest.json", manifest)
    append_receipt(
        destination / "receipts.jsonl",
        {
            "kind": "manifest",
            "batch_id": batch_id,
            "at": manifest["generated_at"],
            "center_domain": snapshot.center_domain,
            "row_count": len(rows),
            "snapshot_file_hash": snapshot.file_hash,
            "auto_transmit": False,
            "auto_confirm": cfg.auto_confirm,
        },
    )
    logger.emit(
        "manifest_built",
        center_domain=snapshot.center_domain,
        rows=len(rows),
        by_status=dict(sorted(counts.items())),
    )
    return manifest
