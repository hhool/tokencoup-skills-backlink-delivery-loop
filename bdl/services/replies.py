"""Classify replies and turn them into proposals - never into events.

Reading a reply is the last manual step in the loop: a human opens the mailbox,
reads what came back, and records ``reply`` with a class. What this service
automates is the part before that judgement: matching a message to the target
domain it belongs to, and saying which signals in the text point to which class.

What it refuses to do is decide. ``pay`` and ``reciprocal`` close a domain forever
(R-510), and even ``no`` ends a conversation. A classifier's error there is not a
wrong row in a table; it is a door shut on a site that was about to link. So every
classification leaves here as a **proposal** with the matched signals shown, and
only a human command (`record --action reply --reply-class ...`) makes it an event.
``--auto-confirm`` writes a proposed ``yes``/``no`` at high confidence - it never
writes a ``pay`` or ``reciprocal``, and never a bounce, because both close things.

Matching order, best evidence first:

  1. ``In-Reply-To`` / ``References`` names one of our ``Message-ID``s - the local
     part is the manifest's idempotency key, which resolves to one row exactly;
  2. the ``From`` domain is a target domain already known to the loop;
  3. nothing matches: the message is reported as unmatched, not guessed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from ..adapters.mailbox import read_messages
from ..models import normalize_domain, domain_from_text

PROPOSAL_KIND = "reply_proposal"
CLASSES = ("yes", "no", "pay", "reciprocal")
AUTO_CONFIRM_CLASSES = ("yes", "no")

# One phrase per signal, so `signals` can name what it saw. Order inside a list
# does not matter; the lists are checked in the order PAY, RECIPROCAL, NO, YES -
# a "we'd love to add it, there is a $50 listing fee" must land on `pay`, not `yes`.
_SIGNALS: List[Tuple[str, List[str]]] = [
    ("pay", [
        r"paid (?:listing|placement|inclusion|submission|review)",
        r"payment (?:is )?required",
        r"\bfee\b", r"\binvoice\b", r"\bcharge", r"\bprice\b", r"\bpricing\b",
        r"sponsored (?:post|content|listing)",
        r"we only (?:add|publish|include|link).{0,40}(?:pay|fee|charge|sponsor)",
        r"listing (?:fee|cost)", r"editorial (?:fee|rate)", r"\bbudget\b",
        r"paid promotion",
    ]),
    ("reciprocal", [
        r"reciprocal", r"link exchange", r"exchang\w+ links?", r"link back",
        r"if you link (?:back )?to us", r"link to us (?:first|too|as well)",
        r"in (?:return|exchange) (?:for|we)", r"trade links?",
        r"add (?:a )?link to (?:our|us)",
    ]),
    ("no", [
        r"not interested", r"no thanks", r"we (?:do not|don't|won't) (?:accept|publish|include|add|link)",
        r"not (?:accepting|a fit|a good fit|relevant)",
        r"(?:we )?declin\w+", r"unable to (?:add|publish|include|accept|link)",
        r"(?:has|have|was|were) (?:been )?removed", r"opt[ -]?out", r"unsubscribe",
        r"no longer accept", r"we have to (?:pass|decline)", r"passed on this",
        r"not something we(?:'ll| will) (?:add|feature)",
    ]),
    ("yes", [
        r"(?:has|have|was|were) (?:been )?(?:added|published|included|approved|placed)",
        r"is now (?:live|up|online)", r"went live", r"(?:link|listing) is live",
        r"thanks? (?:you )?for (?:your |the )?submission",
        r"(?:we(?:'ve| have)|i(?:'ve| have)) added",
        r"happy to (?:add|publish|include|feature)", r"we(?:'d| would) (?:love|be happy) to (?:add|feature|publish)",
        r"(?:your (?:site|page|resource|guide)) (?:is|has been) (?:a great fit|live)",
        r"feel free to submit", r"(?:article|post|piece) (?:is|has been) (?:published|scheduled)",
    ]),
]

# A negation in front of a pay signal inverts it: "no fee", "free of charge",
# "at no cost". Without this, the politest refusals classify as pay.
_SUPPRESS = re.compile(
    r"(?:no|without|free (?:of )?charge|zero|zero|not? any)\s+(?:\w+\s+){0,2}"
    r"(?:fee|charge|cost|payment|invoice)",
    re.I,
)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify(subject: str, body: str) -> Dict[str, Any]:
    """Signals, a class and a confidence, with the evidence named.

    `confidence` is one of high / medium / low. `high` needs two distinct matched
    signals of the same class; a single match is `medium`, and a single match whose
    phrase is a full clause (e.g. "we only add links if you pay") is also medium -
    the operator reads the message; the machine only narrows the search.
    """
    text = " ".join(filter(None, [subject or "", body or ""]))
    suppressed = _SUPPRESS.sub(" ", text)
    result: Dict[str, Any] = {
        "reply_class": "",
        "confidence": "low",
        "signals": [],
        "needs_human": True,
    }
    for reply_class, patterns in _SIGNALS:
        matched: List[str] = []
        for pattern in patterns:
            hit = re.search(pattern, text, re.I)
            if not hit:
                continue
            # A negated fee phrase is a refusal to charge, not an offer to charge:
            # it only counts if the suppression did not erase it.
            if reply_class == "pay" and not re.search(pattern, suppressed, re.I):
                continue
            matched.append(hit.group(0).strip().lower())
        if matched:
            result["reply_class"] = reply_class
            # The matched phrases, not the patterns: a reviewer reads "listing fee",
            # not the regex that found it.
            seen: List[str] = []
            for phrase in matched:
                if phrase not in seen:
                    seen.append(phrase)
            result["signals"] = seen[:5]
            result["confidence"] = "high" if len(seen) >= 2 else "medium"
            break
    # A class with consequences is never confident enough to act on by itself:
    # pay and reciprocal close a domain (R-510), so they are always a proposal.
    if result["reply_class"] in ("pay", "reciprocal"):
        result["needs_human"] = True
    else:
        result["needs_human"] = result["confidence"] != "high"
    return result


def _our_ids(rows: Iterable[Any]) -> Dict[str, str]:
    """idempotency_key -> target domain, from manifest rows when given."""
    mapping: Dict[str, str] = {}
    for row in rows or []:
        key = str(getattr(row, "idempotency_key", "") or "")
        target = normalize_domain(str(getattr(row, "target_domain", "") or ""))
        if key and target:
            mapping[key.lower()] = target
    return mapping


def match_target(
    message: Dict[str, Any],
    known_domains: Iterable[str],
    our_ids: Dict[str, str] = (),
) -> Tuple[str, str]:
    """Resolve one message to a target domain. Returns (domain, matched_via)."""
    for header in (message.get("in_reply_to") or "", message.get("references") or ""):
        for token in re.findall(r"<[^>]+>", header):
            local, _, host = token.strip("<>").partition("@")
            hit = our_ids.get(local.lower()) if local else ""
            if hit:
                return hit, "in_reply_to"
    from_domain = domain_from_text(str(message.get("from") or ""))
    if from_domain:
        for domain in known_domains:
            if from_domain == normalize_domain(str(domain)):
                return from_domain, "from_domain"
    return "", "unmatched"


def propose(
    messages: Sequence[Dict[str, Any]],
    known_domains: Iterable[str],
    rows: Iterable[Any] = (),
    now: Optional[dt.datetime] = None,
    auto_confirm: bool = False,
    out_dir: Union[str, Path] = "",
    proposal_path: Union[str, Path] = "",
) -> Dict[str, Any]:
    """Classify every message and queue a proposal per matched one."""
    moment = now or dt.datetime.now(dt.timezone.utc)
    domains = [normalize_domain(str(item)) for item in known_domains if str(item)]
    mapping = _our_ids(rows)
    proposals: List[Dict[str, Any]] = []
    unmatched: List[Dict[str, Any]] = []
    auto_written: List[Dict[str, Any]] = []

    for message in messages:
        target, via = match_target(message, domains, mapping)
        if message.get("is_dsn"):
            proposals.append({
                "kind": PROPOSAL_KIND,
                "action": "bounced",
                "target_domain": target,
                "address": message.get("address") or "",
                "dsn_status": message.get("dsn_status") or "",
                "matched_via": via,
                "from": message.get("from"),
                "subject": message.get("subject"),
                "source_file": message.get("source_file"),
                "confidence": "high" if message.get("dsn_status") else "medium",
                "signals": ["delivery status notification"],
                "proposed_at": _now(),
                "basis": "the mailbox reports a delivery failure; record it as L2, not as a reply",
            })
            continue
        verdict = classify(str(message.get("subject") or ""), str(message.get("text") or ""))
        if not target:
            unmatched.append({
                "from": message.get("from"),
                "subject": message.get("subject"),
                "source_file": message.get("source_file"),
                "reply_class": verdict["reply_class"],
                "confidence": verdict["confidence"],
                "hint": "no In-Reply-To to a known Message-ID and the From domain is not a target",
            })
            continue
        proposal = {
            "kind": PROPOSAL_KIND,
            "action": "reply",
            "target_domain": target,
            "reply_class": verdict["reply_class"],
            "confidence": verdict["confidence"],
            "needs_human": verdict["needs_human"],
            "matched_via": via,
            "signals": verdict["signals"],
            "address": message.get("address") or "",
            "from": message.get("from"),
            "subject": message.get("subject"),
            "source_file": message.get("source_file"),
            "proposed_at": _now(),
            "basis": "matched signals shown; a human decides the class with `record --action reply`",
        }
        proposals.append(proposal)
        if (
            auto_confirm
            and verdict["reply_class"] in AUTO_CONFIRM_CLASSES
            and verdict["confidence"] == "high"
        ):
            auto_written.append(proposal)

    path = ""
    if proposal_path and not auto_confirm:
        target_path = Path(proposal_path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("a", encoding="utf-8") as handle:
            for proposal in proposals:
                handle.write(json.dumps(proposal, ensure_ascii=False, sort_keys=True) + "\n")
        path = str(target_path)

    return {
        "messages": len(messages),
        "matched": sum(1 for item in proposals if item.get("target_domain")),
        "unmatched": unmatched,
        "proposals": proposals,
        "proposal_queue": path,
        "auto_confirm": auto_confirm,
        "auto_confirmable": [
            {
                "target_domain": item["target_domain"],
                "reply_class": item["reply_class"],
                "confidence": item["confidence"],
            }
            for item in auto_written
        ],
        "note": (
            "A classification is a proposal, never an event. `pay` and `reciprocal` "
            "close a domain under R-510, so they always wait for a human, even at "
            "high confidence; --auto-confirm only writes yes/no at high confidence."
        ),
    }


def propose_from_path(
    path: Union[str, Path],
    known_domains: Iterable[str],
    rows: Iterable[Any] = (),
    **kwargs: Any,
) -> Dict[str, Any]:
    return propose(read_messages(path), known_domains, rows, **kwargs)
