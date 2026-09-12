"""Unattended runs. run_daily drives the whole pipeline, run_digest only the known boards."""

import asyncio
import os
import sys
import uuid

from dotenv import load_dotenv

from agents.scorer import score_all
from core.filters import apply_filters
from core.notify import render_digest, send_digest
from core.store import (connect, init_db, load_profile, save_run, save_scores, set_syndicated,
                        unseen_posting_ids, upsert_postings)
from core.verify import BudgetGuard, verify_scores
from pipeline import rank, replay_latest, run_full
from sources.ats import fetch_boards
from sources.slugs import all_known_boards
from sources.syndication import check

# cron runs with a nearly empty environment, so the .env is loaded explicitly
load_dotenv()


async def run_digest(log=print) -> dict:
    """Skips A2 and the slug resolver, and searches the same known boards a full run does."""
    init_db()
    conn = connect()
    run_id = uuid.uuid4().hex[:12]

    profile = load_profile(conn)
    if profile is None:
        conn.close()
        raise SystemExit("no saved profile, run the app once before scheduling the digest")

    boards = all_known_boards(conn)
    if not boards:
        conn.close()
        raise SystemExit("no known boards, run the app once before scheduling the digest")

    postings, reached, failed = await fetch_boards(boards)
    log(f"{reached} of {reached + failed} boards reachable, {len(postings)} postings")

    unseen = unseen_posting_ids(conn, [p.posting_id for p in postings])
    new = [p for p in postings if p.posting_id in unseen]
    log(f"{len(new)} genuinely new since the last run")

    upsert_postings(conn, postings)
    funnel = {"boards_reached": reached, "boards_failed": failed,
              "postings_fetched": len(postings), "new_postings": len(new)}

    if not new:
        save_run(conn, run_id, "digest", funnel, 0, 0, 0.0, emailed=0)
        conn.close()
        log("nothing new, no email sent")
        return {"run_id": run_id, "new": 0, "emailed": False, "results": []}

    syndication = await check(new)
    set_syndicated(conn, syndication)
    for posting in new:
        posting.syndicated = syndication.get(posting.posting_id)

    survivors, dropped = apply_filters(
        new, profile.desired_role, profile.level, profile.work_mode, profile.location)
    funnel.update({"after_hard_filter": len(survivors),
                   **{f"dropped_{k}": v for k, v in dropped.items()}})
    log(f"{len(new)} -> {len(survivors)} after the hard filter")

    guard = BudgetGuard()
    proposals, halted = await score_all(survivors, profile.resume_text, guard)
    scores, gate_stats = verify_scores(
        proposals, {p.posting_id for p in survivors},
        {p.posting_id: p.description for p in survivors}, profile.resume_text, run_id)
    funnel.update(gate_stats)
    save_scores(conn, scores)

    ranked = rank(scores, {p.posting_id: p for p in new}, profile.location)
    body = render_digest(ranked, funnel, run_id)
    subject = f"EaseApply: {len(ranked)} new {'match' if len(ranked) == 1 else 'matches'}"
    emailed = send_digest(body, subject) if ranked else False
    log(f"digest {'sent' if emailed else 'rendered but not sent'}, {len(ranked)} matches")

    save_run(conn, run_id, "digest", funnel, len(new), guard.tokens, guard.cost_usd,
             emailed=int(emailed))
    conn.close()
    return {"run_id": run_id, "new": len(new), "emailed": emailed, "results": ranked,
            "html": body, "halted": halted}


def resend_latest(log=print) -> dict:
    """Emails the latest stored run as it stands. No boards fetched and no model called."""
    out = replay_latest()
    rows = out["results"][:12]
    if not rows:
        log("no stored results to send")
        return {"run_id": out["run_id"], "sent": 0, "emailed": False}
    plural = "match" if len(rows) == 1 else "matches"
    body = render_digest(rows, out["funnel"], out["run_id"],
                         heading=f"Your top {len(rows)} {plural}")
    emailed = send_digest(body, f"EaseApply: your top {len(rows)} {plural}")
    log(f"resend {'sent' if emailed else 'not sent'}, {len(rows)} {plural}")
    return {"run_id": out["run_id"], "sent": len(rows), "emailed": emailed, "html": body}


async def run_daily(log=print) -> dict:
    """The whole pipeline on a schedule, then an email about what is genuinely new.

    Unlike run_digest this runs A2, so new employers are discovered rather than only known boards.
    """
    init_db()
    conn = connect()
    profile = load_profile(conn)
    # what has been scored before, so widening the preferences also surfaces older postings
    already_scored = {r["posting_id"] for r in
                      conn.execute("SELECT DISTINCT posting_id FROM scores")}
    conn.close()
    if profile is None:
        raise SystemExit("no saved profile, run the app once before scheduling the daily run")

    out = await run_full(
        desired_role=profile.desired_role, level=profile.level, work_mode=profile.work_mode,
        location=profile.location, extra_context=profile.extra_context or "",
        resume_text=profile.resume_text, emit=lambda message, **_: log(message),
    )
    # new means never scored for you, not merely never fetched, so a preference change counts
    fresh = [r for r in out["results"] if r["posting_id"] not in already_scored]
    body = render_digest(fresh, out["funnel"], out["run_id"])
    plural = "match" if len(fresh) == 1 else "matches"
    emailed = send_digest(body, f"EaseApply: {len(fresh)} new {plural}") if fresh else False
    log(f"daily run {'sent' if emailed else 'rendered but not sent'}, {len(fresh)} new {plural}")
    return {"run_id": out["run_id"], "new": len(fresh), "emailed": emailed,
            "results": fresh, "html": body, "tokens": out["tokens"],
            "cost_usd": out["cost_usd"], "halted": out["halted"]}


def _main() -> None:
    dry_run = "--dry-run" in sys.argv
    if dry_run:
        os.environ["EMAIL_BACKEND"] = "none"
    runner = run_digest if "--known-boards-only" in sys.argv else run_daily
    out = asyncio.run(runner())
    if dry_run and out.get("html"):
        with open("digest_preview.html", "w") as fh:
            fh.write(out["html"])
        print("preview written to digest_preview.html")


if __name__ == "__main__":
    _main()
