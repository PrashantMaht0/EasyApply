"""Deterministic outer loop. Every branch here is a Python predicate, never a model decision."""

import asyncio
import json
import sys
import uuid

from pypdf import PdfReader
from dotenv import load_dotenv

from agents.ranker import ranking_rationale
from agents.prompts import versions
from agents.profiler import build_profile, profile_json
from agents.scorer import score_all
from agents.sourcer import propose_companies
from agents.synthesist import analyse_gaps
from core.filters import apply_filters, desired_cities, in_city, location_rank
from core.models import Posting, Profile
from core.store import (connect, init_db, latest_run, load_profile, load_scores, save_profile,
                        save_run, save_scores, set_syndicated, unseen_posting_ids,
                        upsert_postings)
from core.trace import RunTrace
from core.verify import BudgetGuard, hidden_gem, verify_scores
from sources.ats import fetch_boards
from sources.slugs import all_known_boards, known_company_names, resolve
from sources.syndication import check

# .env is read here so a cron or CLI run picks up the same settings as the UI
load_dotenv()


def extract_resume_text(pdf_path: str) -> str:
    """Verbatim text extraction. This becomes the source of truth for resume span checks."""
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def postings_from_rows(rows) -> list[Posting]:
    return [
        Posting(
            posting_id=r["posting_id"], company_slug=r["company_slug"], platform=r["platform"],
            title=r["title"], location=r["location"] or "", url=r["url"],
            description=r["description"] or "", work_mode=r["work_mode"],
            posted_at=r["posted_at"], syndicated=r["syndicated"],
        )
        for r in rows
    ]


def collapse_duplicates(ranked: list[dict]) -> list[dict]:
    """One row per company and title. Boards list the same role once per location."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in ranked:
        # Hidden Gem is part of the key, so a gem is never merged into a row that is not one
        groups.setdefault(
            (r["company"], r["title"].strip().lower(), r["hidden_gem"]), []).append(r)
    collapsed = []
    for members in groups.values():
        # the closest posting represents the group, then the best scoring one
        best = max(members, key=lambda r: (-r.get("proximity", 3), r["fit_score"],
                                           r["verified_ratio"]))
        # the representative is the closest posting, so its own location leads the list
        locations = list(dict.fromkeys(
            [best["location"]] + [m["location"] for m in members if m["location"]]))
        locations = [loc for loc in locations if loc]
        collapsed.append({**best, "locations": locations, "duplicates": len(members),
                          "new": any(m.get("new") for m in members)})
    return sorted(collapsed, key=lambda r: (r.get("proximity", 3), -r["fit_score"]))


PROXIMITY = {0: "in your city", 1: "in country", 2: "in region",
             3: "remote or unstated", 4: "elsewhere"}


def rank(scores, postings_by_id, desired_location: str = "",
         fresh: set[str] | None = None) -> list[dict]:
    """Location first, then fit. A reachable role outranks a better scoring unreachable one."""
    ranked = []
    for s in sorted(scores, key=lambda x: x.fit_score, reverse=True):
        p = postings_by_id.get(s.posting_id)
        if p is None:
            continue
        ranked.append({
            "posting_id": s.posting_id, "title": p.title, "company": p.company_slug,
            "url": p.url, "location": p.location, "fit_score": s.fit_score,
            "matched": s.matched, "missing": s.missing, "sponsorship": s.sponsorship,
            "verified_ratio": s.verified_ratio,
            "hidden_gem": hidden_gem(p.posted_at, p.syndicated, s.fit_score),
            "work_mode": p.work_mode or "not stated",
            "proximity": location_rank(desired_location, p),
            "new": s.posting_id in (fresh or set()),
        })
    return collapse_duplicates(ranked)


async def run_full(desired_role: str, level: str, work_mode: str, location: str,
                   extra_context: str, resume_text: str, emit=None,
                   run_id: str | None = None) -> dict:
    """Full discovery run. Stages execute in this fixed order and nothing reorders them."""
    emit = emit or (lambda message, **_: print(message))
    init_db()
    conn = connect()
    run_id = run_id or uuid.uuid4().hex[:12]
    run_trace = RunTrace(run_id)
    guard = BudgetGuard()
    funnel = {}

    emit("A1 profiling resume and intake form")
    profile = build_profile(desired_role, level, work_mode, location, extra_context,
                            resume_text, guard)
    if profile is None:
        conn.close()
        raise RuntimeError("A1 profile failed the schema guard twice")
    save_profile(conn, Profile(
        desired_role=desired_role, level=level, work_mode=work_mode, location=location,
        extra_context=extra_context, resume_text=resume_text,
        profile_json=profile_json(profile),
    ))

    emit("A2 proposing employers")
    companies = propose_companies(profile, desired_role, location, work_mode, guard,
                                  exclude=known_company_names())
    funnel["companies_proposed"] = len(companies)
    emit(f"{len(companies)} employers proposed", stage="sourcer", companies_proposed=len(companies))

    boards, slug_stats = await resolve(companies)
    # Every board we have ever confirmed is searched, not only the companies A2 named this run.
    # Fetching is free and takes seconds, and the hard filter still caps what reaches a model.
    seen = set(boards)
    carried = [b for b in all_known_boards(conn) if b not in seen]
    boards = list(boards) + carried
    slug_stats["carried_over"] = len(carried)
    slug_stats["searched"] = len(boards)
    funnel.update({f"slug_{k}": v for k, v in slug_stats.items()})
    emit(f"{slug_stats['proposed']} proposed, {slug_stats['resolved']} resolved, "
         f"{slug_stats['from_seed']} from seed, {slug_stats['from_cache']} from cache, "
         f"{len(carried)} carried over, {len(boards)} searched",
         stage="slug_resolver", **{f"slug_{k}": v for k, v in slug_stats.items()})

    postings, reached, failed = await fetch_boards(boards)
    funnel.update({"boards_reached": reached, "boards_failed": failed,
                   "postings_after_dedupe": len(postings)})
    emit(f"{reached} of {reached + failed} boards reachable, {len(postings)} postings after dedupe",
         stage="ats_fanout", boards_reached=reached, boards_failed=failed,
         postings_after_dedupe=len(postings))

    syndication = await check(postings)
    # kept on the objects too, so ranking never reloads the whole postings table
    for p in postings:
        p.syndicated = syndication.get(p.posting_id)
    unsyndicated = sum(1 for v in syndication.values() if v == 0)
    funnel["unsyndicated"] = unsyndicated
    emit(f"{unsyndicated} not found on any aggregator", stage="syndication",
         unsyndicated=unsyndicated)

    fresh = unseen_posting_ids(conn, [p.posting_id for p in postings])
    funnel["new_since_last_run"] = len(fresh)
    emit(f"{len(fresh)} postings never seen before", stage="diff", new_postings=len(fresh))
    upsert_postings(conn, postings)
    set_syndicated(conn, syndication)

    survivors, dropped = apply_filters(postings, desired_role, level, work_mode, location)
    funnel.update({"after_hard_filter": len(survivors),
                   **{f"dropped_{k}": v for k, v in dropped.items()}})
    cities = desired_cities(location)
    if cities:
        raw_here = sum(1 for p in postings if in_city(cities, p))
        matching_here = sum(1 for p in survivors if in_city(cities, p))
        nearby = sum(1 for p in survivors if location_rank(location, p) == 1)
        named = ", ".join(sorted(cities)).title()
        funnel.update({"city_matching": matching_here, "city_postings": raw_here,
                       "country_matching": nearby})
        emit(f"{matching_here} of {raw_here} postings in {named} match this role, "
             f"{nearby} more elsewhere in the country", stage="city_check",
             city_matching=matching_here, city_postings=raw_here, country_matching=nearby)

    emit(f"{len(postings)} -> {len(survivors)} after the hard filter", stage="hard_filter",
         items_in=len(postings), after_hard_filter=len(survivors),
         **{f"dropped_{k}": v for k, v in dropped.items()})

    proposals, halted = await score_all(
        survivors, resume_text, guard,
        on_batch=lambda done, total, n: emit(f"batch {done} of {total} scored",
                                            stage="scorer", batches_done=done, batches_total=total),
    )
    if halted:
        emit("budget ceiling reached, partial results kept")

    descriptions = {p.posting_id: p.description for p in survivors}
    scores, gate_stats = verify_scores(proposals, {p.posting_id for p in survivors},
                                       descriptions, resume_text, run_id)
    funnel.update(gate_stats)
    emit(f"{gate_stats['claims_verified']} of {gate_stats['claims_proposed']} claims verified",
         stage="verification_gate", **gate_stats)

    save_scores(conn, scores)
    postings_by_id = {p.posting_id: p for p in postings}
    ranked = rank(scores, postings_by_id, location, fresh)
    funnel["hidden_gems"] = sum(1 for r in ranked if r["hidden_gem"])

    emit("A4 cross portfolio gap analysis")
    gaps = analyse_gaps(scores, guard, {p.posting_id: p.company_slug for p in survivors}) or {}

    emit("A0 ranking rationale")
    rationale = ranking_rationale(ranked, desired_role, guard)

    funnel["prompt_versions"] = versions()
    funnel["agents"] = guard.agents
    funnel["tokens"] = guard.tokens
    funnel["cost_usd"] = round(guard.cost_usd, 4)
    save_run(conn, run_id, "full", {**funnel, "rationale": rationale, "gaps": gaps},
             len(postings), guard.tokens, guard.cost_usd)
    conn.close()
    emit(f"run {run_id} complete, {guard.tokens} tokens, ${guard.cost_usd:.4f}",
         stage="done", tokens=guard.tokens, cost_usd=round(guard.cost_usd, 4))
    # closed last, so every stage span above is a child of the run span
    run_trace.finish(results=len(ranked), tokens=guard.tokens,
                     cost_usd=round(guard.cost_usd, 4),
                     postings=len(postings), boards=slug_stats["searched"])
    return {"run_id": run_id, "funnel": funnel, "results": ranked, "gaps": gaps,
            "rationale": rationale, "tokens": guard.tokens, "cost_usd": guard.cost_usd,
            "halted": halted}


def replay_latest() -> dict:
    """Demo mode. Rebuilds a stored run from the blackboard with no network and no model call."""
    conn = connect()
    run = latest_run(conn)
    if run is None:
        conn.close()
        raise RuntimeError("no stored run to replay, the demo database is empty")
    funnel = json.loads(run["funnel"] or "{}")
    scores = load_scores(conn, run["run_id"])
    postings = postings_from_rows(conn.execute(
        "SELECT * FROM postings WHERE posting_id IN (SELECT posting_id FROM scores WHERE run_id = ?)",
        (run["run_id"],)).fetchall())
    saved = load_profile(conn)
    conn.close()
    return {
        "run_id": run["run_id"], "funnel": funnel,
        "results": rank(scores, {p.posting_id: p for p in postings},
                        saved.location if saved else ""),
        "gaps": funnel.get("gaps") or {}, "rationale": funnel.get("rationale", ""),
        "tokens": run["tokens"] or 0, "cost_usd": run["cost_usd"] or 0.0, "halted": False,
    }


def _main() -> None:
    if len(sys.argv) < 3:
        print('usage: python pipeline.py <resume.pdf> "<desired role>" [level] [work_mode] [location]')
        raise SystemExit(1)
    resume_text = extract_resume_text(sys.argv[1])
    role = sys.argv[2]
    level = sys.argv[3] if len(sys.argv) > 3 else "mid"
    work_mode = sys.argv[4] if len(sys.argv) > 4 else "any"
    location = sys.argv[5] if len(sys.argv) > 5 else ""
    out = asyncio.run(run_full(role, level, work_mode, location, "", resume_text))
    if out["rationale"]:
        print(f"\n{out['rationale']}")
    print(f"\ntop matches for run {out['run_id']}")
    for r in out["results"][:10]:
        gem = " [hidden gem]" if r["hidden_gem"] else ""
        print(f"  {r['fit_score']:3d}  {r['company']:<14} {r['title']}{gem}")
        print(f"       verified {r['verified_ratio']:.0%}  {r['url']}")


if __name__ == "__main__":
    _main()
