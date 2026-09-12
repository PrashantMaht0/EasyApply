"""The safety boundary. A model output is an uncommitted proposal until this module accepts it."""

import json
import os
import re
import threading
import unicodedata
from datetime import datetime, timezone

from core.models import Score

# Bedrock on demand prices per million tokens, matched in order against the model id.
# Nova 2 Lite is billed at the Nova Lite rate here, confirm it if the run cost matters.
PRICES = [
    ("nova-micro", (0.035, 0.14)),
    ("nova-premier", (2.50, 12.50)),
    ("nova-pro", (0.80, 3.20)),
    ("nova", (0.06, 0.24)),
    ("haiku", (1.00, 5.00)),
    ("opus", (15.00, 75.00)),
    ("sonnet", (3.00, 15.00)),
]
DEFAULT_BUDGET_USD = 0.50

_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)
_WS = re.compile(r"\s+")
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"',
           "–": "-", "—": "-", " ": " "}


class BudgetExceeded(Exception):
    """Raised by the V8 guard before a call that would breach the ceiling."""


def parse_json(text: str) -> dict | list | None:
    """V4 part one. Tolerates a code fence or prose around the object."""
    if not text:
        return None
    fenced = _FENCE.search(text)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (candidate.find("{"), candidate.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    end = max(candidate.rfind("}"), candidate.rfind("]"))
    try:
        return json.loads(candidate[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        return _repair_truncated(candidate[start:])


def _repair_truncated(text: str):
    """A reply cut off at the token cap is still usable up to its last complete element."""
    for cut in range(len(text), 0, -1):
        if text[cut - 1] not in "\"}]":
            continue
        stub = text[:cut].rstrip().rstrip(",")
        closing = "".join(
            "}" if ch == "{" else "]"
            for ch in reversed(_unclosed(stub))
        )
        try:
            return json.loads(stub + closing)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _unclosed(text: str) -> list[str]:
    """Brackets still open, ignoring anything inside a string literal."""
    stack, in_string, escaped = [], False, False
    for ch in text:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string and ch in "{[":
            stack.append(ch)
        elif not in_string and ch in "}]" and stack:
            stack.pop()
    return stack


def schema_guard(obj, spec: dict[str, type]) -> bool:
    """V4 part two. Required fields present with the right types."""
    if not isinstance(obj, dict):
        return False
    return all(field in obj and isinstance(obj[field], kind) for field, kind in spec.items())


def known_ids_only(results: list[dict], requested: set[str]) -> tuple[list[dict], int]:
    """V5. An ID the model invented was never in a real ATS feed, so it cannot exist."""
    kept = [r for r in results if r.get("posting_id") in requested]
    return kept, len(results) - len(kept)


def normalise_span(text: str) -> str:
    """Whitespace, case and unicode punctuation folded so quoting style cannot fail a claim."""
    text = unicodedata.normalize("NFKC", text or "")
    for bad, good in _QUOTES.items():
        text = text.replace(bad, good)
    return _WS.sub(" ", text).strip().lower()


def span_present(span: str, source: str) -> bool:
    """V6. The quote must actually occur in the stored source text."""
    span = normalise_span(span)
    return bool(span) and span in normalise_span(source)


def verify_claims(claims: list[dict], job_text: str, resume_text: str) -> tuple[list[dict], int]:
    """Drops any claim whose quotes cannot be located. Returns survivors and the dropped count."""
    kept = []
    for claim in claims:
        if not isinstance(claim, dict) or not claim.get("skill"):
            continue
        if not span_present(claim.get("job_span", ""), job_text):
            continue
        resume_span = claim.get("resume_span")
        if resume_span and not span_present(resume_span, resume_text):
            continue
        kept.append(claim)
    return kept, len(claims) - len(kept)


def clamp_score(value) -> tuple[int, bool]:
    """V7. Returns the clamped score and whether it was out of range."""
    try:
        score = int(value)
    except (TypeError, ValueError):
        return 0, True
    clamped = max(0, min(100, score))
    return clamped, clamped != score


def verify_sponsorship(sponsorship, job_text: str) -> dict:
    """'mentioned' is only allowed to survive when its span passes V6."""
    if not isinstance(sponsorship, dict):
        return {"status": "not_mentioned"}
    if sponsorship.get("status") != "mentioned":
        return {"status": "not_mentioned"}
    span = sponsorship.get("span", "")
    if span_present(span, job_text):
        return {"status": "mentioned", "span": span}
    return {"status": "not_mentioned"}


class BudgetGuard:
    """V8. Tracks cumulative spend and halts the run before the ceiling is breached."""

    def __init__(self, ceiling_usd: float | None = None):
        self.ceiling = ceiling_usd if ceiling_usd is not None else float(
            os.getenv("EASEAPPLY_BUDGET_USD") or DEFAULT_BUDGET_USD
        )
        self.tokens = 0
        self.cost_usd = 0.0
        self.agents: dict[str, dict] = {}
        # scorer batches record from several threads at once
        self._lock = threading.Lock()

    def record(self, model_id: str, input_tokens: int, output_tokens: int,
               agent: str = "", seconds: float = 0.0) -> None:
        with self._lock:
            self.tokens += input_tokens + output_tokens
            if agent:
                stats = self.agents.setdefault(agent, {"calls": 0, "tokens": 0, "seconds": 0.0})
                stats["calls"] += 1
                stats["tokens"] += input_tokens + output_tokens
                stats["seconds"] = round(stats["seconds"] + seconds, 2)
            name = (model_id or "").lower()
            # a locally hosted model costs nothing, so only priced tiers add to the ceiling
            prices = next((p for key, p in PRICES if key in name), None)
            if prices is not None:
                self.cost_usd += (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000

    def check(self) -> None:
        if self.cost_usd >= self.ceiling:
            raise BudgetExceeded(
                f"run cost ${self.cost_usd:.4f} reached the ${self.ceiling:.2f} ceiling"
            )


def verify_scores(proposals: list[dict], requested: set[str], descriptions: dict[str, str],
                  resume_text: str, run_id: str):
    """The full gate. V5 then V6 then V7, and only survivors become Score rows."""
    kept, unknown_ids = known_ids_only(proposals, requested)
    scores, claims_total, claims_kept, clamped = [], 0, 0, 0
    for item in kept:
        job_text = descriptions.get(item["posting_id"], "")
        matched, _ = verify_claims(item.get("matched") or [], job_text, resume_text)
        missing, _ = verify_claims(item.get("missing") or [], job_text, resume_text)
        proposed = len(item.get("matched") or []) + len(item.get("missing") or [])
        survived = len(matched) + len(missing)
        claims_total += proposed
        claims_kept += survived
        score, was_clamped = clamp_score(item.get("fit_score"))
        clamped += int(was_clamped)
        scores.append(Score(
            posting_id=item["posting_id"], run_id=run_id, fit_score=score,
            matched=matched, missing=missing,
            sponsorship=verify_sponsorship(item.get("sponsorship"), job_text),
            verified_ratio=(survived / proposed) if proposed else 0.0,
        ))
    stats = {
        "proposals": len(proposals),
        "unknown_ids_dropped": unknown_ids,
        "claims_proposed": claims_total,
        "claims_verified": claims_kept,
        "scores_clamped": clamped,
    }
    return scores, stats


def hidden_gem(posted_at: str | None, syndicated: int | None, fit_score: int,
               max_age_days: int = 7, min_score: int = 70) -> bool:
    """Computed, never asserted by a model. A NULL syndication check can never qualify."""
    if syndicated != 0 or fit_score < min_score or not posted_at:
        return False
    try:
        posted = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - posted).days
    return 0 <= age_days <= max_age_days
