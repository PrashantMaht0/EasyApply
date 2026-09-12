"""Public ATS board fetchers and per platform normalisers. No credentials, no scraping."""

import asyncio
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone

import httpx

from core.filters import REMOTE
from core.models import Posting

USER_AGENT = "EaseApply/0.1 (job search assistant; contact via repository)"
TIMEOUT = 8.0
MAX_CONCURRENCY = 8

BOARD_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    # language=en is what makes Personio return descriptions, without it they come back empty
    "personio": "https://{slug}.jobs.personio.de/search.json?language=en",
}

ID_PREFIX = {"greenhouse": "gh", "lever": "lv", "ashby": "ab", "personio": "pe"}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")
_HYBRID = re.compile(r"\bhybrid\b", re.I)


def clean_html(raw: str) -> str:
    """Unescape then strip tags. Runs before storage so the stored text is the verify source."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6])\s*/?>", "\n", text, flags=re.I)
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = _WS.sub(" ", text)
    return _BLANKS.sub("\n\n", text).strip()


def _work_mode(*hints: str | None) -> str | None:
    """Best effort work mode from whatever the platform exposes."""
    blob = " ".join(h for h in hints if h)
    if not blob:
        return None
    if REMOTE.search(blob):
        return "remote"
    if _HYBRID.search(blob):
        return "hybrid"
    if blob.strip().lower() in ("onsite", "on-site"):
        return "onsite"
    return None


def _payload_hash(company_slug: str, title: str, location: str, description: str) -> str:
    blob = "|".join((company_slug, title, location, description))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _safe_url(url: str) -> str:
    """Board links reach the UI and the digest email, so only http and https are kept."""
    return url if (url or "").startswith(("https://", "http://")) else ""


def _build(platform: str, slug: str, native_id: str, title: str, location: str,
           url: str, description: str, posted_at: str | None,
           mode_hints: tuple[str | None, ...]) -> Posting:
    return Posting(
        posting_id=f"{ID_PREFIX[platform]}_{native_id}",
        company_slug=slug,
        platform=platform,
        title=title.strip(),
        location=location.strip(),
        url=_safe_url(url),
        description=description,
        work_mode=_work_mode(location, *mode_hints),
        posted_at=posted_at,
        payload_hash=_payload_hash(slug, title.strip(), location.strip(), description),
    )


def _drop_undescribed(postings: list[Posting]) -> list[Posting]:
    """No description means no source for span verification, so the posting is unusable."""
    return [p for p in postings if p.description]


def normalise_greenhouse(slug: str, raw: dict) -> list[Posting]:
    out = []
    for job in raw.get("jobs", []):
        location = (job.get("location") or {}).get("name", "")
        out.append(_build(
            "greenhouse", slug, str(job["id"]), job.get("title", ""), location,
            job.get("absolute_url", ""), clean_html(job.get("content", "")),
            job.get("first_published") or job.get("updated_at"), (),
        ))
    return _drop_undescribed(out)


def normalise_lever(slug: str, raw: list) -> list[Posting]:
    out = []
    for job in raw:
        categories = job.get("categories") or {}
        sections = [job.get("descriptionPlain", "")]
        sections += [clean_html(s.get("content", "")) for s in job.get("lists", [])]
        sections.append(job.get("additionalPlain", ""))
        created = job.get("createdAt")
        posted = (
            datetime.fromtimestamp(created / 1000, timezone.utc).isoformat(timespec="seconds")
            if created else None
        )
        out.append(_build(
            "lever", slug, str(job["id"]), job.get("text", ""),
            categories.get("location", ""), job.get("hostedUrl", ""),
            clean_html("\n\n".join(s for s in sections if s)), posted,
            (job.get("workplaceType"),),
        ))
    return _drop_undescribed(out)


def normalise_ashby(slug: str, raw: dict) -> list[Posting]:
    out = []
    for job in raw.get("jobs", []):
        if job.get("isListed") is False:
            continue
        out.append(_build(
            "ashby", slug, str(job["id"]), job.get("title", ""), job.get("location", ""),
            job.get("jobUrl", ""), clean_html(job.get("descriptionPlain", "")),
            job.get("publishedAt"),
            # isRemote says the role has a remote option, which is a work mode fact. Geography
            # is decided separately in core/filters.py from the location string.
            (job.get("workplaceType"), "remote" if job.get("isRemote") else None),
        ))
    return _drop_undescribed(out)


def normalise_personio(slug: str, raw: list) -> list[Posting]:
    out = []
    for job in raw:
        # offices read "Berlin, Germany | hybrid", so the mode is split off from the place
        offices = job.get("offices") or ([job["office"]] if job.get("office") else [])
        places, modes = [], []
        for office in offices:
            place, _, mode = office.partition("|")
            places.append(place.strip())
            if mode.strip():
                modes.append(mode.strip())
        out.append(_build(
            "personio", slug, str(job["id"]), job.get("name", ""), "; ".join(places),
            f"https://{slug}.jobs.personio.de/job/{job['id']}",
            clean_html(job.get("description", "")), None, tuple(modes),
        ))
    return _drop_undescribed(out)


NORMALISERS = {
    "greenhouse": normalise_greenhouse,
    "lever": normalise_lever,
    "ashby": normalise_ashby,
    "personio": normalise_personio,
}


def dedupe(postings: list[Posting]) -> list[Posting]:
    """Drop repeat posting_ids, then identical payloads listed under two boards."""
    seen_ids, seen_hashes, out = set(), set(), []
    for p in postings:
        if p.posting_id in seen_ids or p.payload_hash in seen_hashes:
            continue
        seen_ids.add(p.posting_id)
        seen_hashes.add(p.payload_hash)
        out.append(p)
    return out


async def fetch_board(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                      slug: str, platform: str) -> list[Posting] | None:
    """Returns normalised postings, or None when the board could not be reached."""
    async with sem:
        try:
            resp = await client.get(BOARD_URL[platform].format(slug=slug))
            resp.raise_for_status()
            return NORMALISERS[platform](slug, resp.json())
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError):
            return None


async def fetch_boards(boards: list[tuple[str, str]]) -> tuple[list[Posting], int, int]:
    """Fan out across resolved boards. Reports failures rather than hiding them."""
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        results = await asyncio.gather(
            *(fetch_board(client, sem, slug, platform) for slug, platform in boards)
        )
    postings, reached = [], 0
    for result in results:
        if result is None:
            continue
        reached += 1
        postings.extend(result)
    return dedupe(postings), reached, len(boards) - reached


def _main() -> None:
    if len(sys.argv) < 2:
        print("usage: python -m sources.ats <slug> [platform]")
        raise SystemExit(1)
    slug = sys.argv[1]
    platforms = [sys.argv[2]] if len(sys.argv) > 2 else list(BOARD_URL)
    postings, reached, failed = asyncio.run(
        fetch_boards([(slug, p) for p in platforms])
    )
    print(f"{reached} of {reached + failed} boards reachable, {len(postings)} postings")
    for p in postings[:5]:
        print(f"\n{p.posting_id}  [{p.platform}]  {p.title}")
        print(f"  location={p.location!r} work_mode={p.work_mode} posted_at={p.posted_at}")
        print(f"  {p.url}")
        print(f"  {p.description[:200]!r}")


if __name__ == "__main__":
    _main()
