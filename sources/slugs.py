"""Company name to ATS board resolution. Deterministic candidates, probed and cached."""

import asyncio
import json
import os
import re
import sys

import httpx

from core.store import active_boards, cached_probes, connect, init_db, upsert_company
from sources.ats import MAX_CONCURRENCY, TIMEOUT, USER_AGENT

SEED_PATH = "data/seed_slugs.json"

# Probed without content=true, which is roughly twelve times smaller than a full fetch
PROBE_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json&limit=1",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "personio": "https://{slug}.jobs.personio.de/search.json",
}

PLATFORMS = ["greenhouse", "lever", "ashby", "personio"]

_SUFFIXES = {"inc", "inc.", "llc", "ltd", "ltd.", "corp", "corp.", "co", "co.",
             "company", "gmbh", "limited", "plc", "labs", "technologies", "the"}
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def candidates(name: str) -> list[str]:
    """Slug variants a company is likely to use, most probable first."""
    cleaned = _NON_ALNUM.sub(" ", name.lower())
    words = [w for w in cleaned.split() if w not in _SUFFIXES]
    if not words:
        return []
    out = ["".join(words)]
    if len(words) > 1:
        out.append("-".join(words))
        out.append(words[0])
    return list(dict.fromkeys(out))


def _norm_name(name: str) -> str:
    cleaned = _NON_ALNUM.sub("", name.lower())
    return cleaned.replace(" ", "")


def load_seed() -> dict[str, dict]:
    if not os.path.exists(SEED_PATH):
        return {}
    with open(SEED_PATH) as fh:
        content = fh.read().strip()
    return json.loads(content) if content else {}


def known_company_names() -> list[str]:
    """Employers already on file, so A2 can spend its proposals on new ones."""
    return sorted({entry.get("name", key) for key, entry in load_seed().items()})


def all_known_boards(conn) -> list[tuple[str, str]]:
    """Every board ever confirmed, from the seed file and the probe cache, deduplicated."""
    seeded = [(entry["slug"], entry["platform"]) for entry in load_seed().values()]
    return list(dict.fromkeys(seeded + active_boards(conn)))


async def probe(client: httpx.AsyncClient, sem: asyncio.Semaphore,
                name: str, slug: str, platform: str) -> bool:
    """True when the board exists, has postings, and identifies as the proposed company."""
    async with sem:
        try:
            resp = await client.get(PROBE_URL[platform].format(slug=slug))
            if resp.status_code != 200:
                return False
            data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return False
    jobs = data if isinstance(data, list) else data.get("jobs", [])
    if not jobs:
        return False
    # Only Greenhouse returns a company name, so it is the only platform we can cross-check
    returned = jobs[0].get("company_name") if platform == "greenhouse" else None
    if returned and _norm_name(returned) != _norm_name(name):
        return False
    return True


async def resolve(names: list[str]) -> tuple[list[tuple[str, str]], dict]:
    """Seed, then cache, then network. Returns resolved boards plus funnel counts."""
    init_db()
    seed = load_seed()
    conn = connect()
    cache = cached_probes(conn)

    boards: dict[str, tuple[str, str]] = {}
    from_seed = from_cache = 0
    pending: list[tuple[str, str, str]] = []

    for name in names:
        hit = seed.get(name.lower())
        if hit:
            boards[name] = (hit["slug"], hit["platform"])
            # the digest reads its board list from this table, so a seed hit belongs in it too
            upsert_company(conn, hit["slug"], hit["platform"], name, 1)
            from_seed += 1
            continue
        cached_hit = next(
            ((s, p) for s in candidates(name) for p in PLATFORMS if cache.get((s, p)) == 1),
            None,
        )
        if cached_hit:
            boards[name] = cached_hit
            from_cache += 1
            continue
        pending += [
            (name, s, p)
            for s in candidates(name)
            for p in PLATFORMS
            if (s, p) not in cache
        ]

    if pending:
        sem = asyncio.Semaphore(MAX_CONCURRENCY)
        async with httpx.AsyncClient(
            timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            results = await asyncio.gather(
                *(probe(client, sem, n, s, p) for n, s, p in pending)
            )
        for (name, slug, platform), ok in zip(pending, results):
            upsert_company(conn, slug, platform, name, 1 if ok else 0)
            if ok and name not in boards:
                boards[name] = (slug, platform)

    conn.close()
    stats = {
        "proposed": len(names),
        "resolved": len(boards),
        "from_seed": from_seed,
        "from_cache": from_cache,
        "probed": len(pending),
    }
    return list(boards.values()), stats


def _main() -> None:
    if len(sys.argv) < 2:
        print('usage: python -m sources.slugs "Company One" "Company Two"')
        raise SystemExit(1)
    names = sys.argv[1:]
    boards, stats = asyncio.run(resolve(names))
    print(
        f"{stats['proposed']} proposed, {stats['resolved']} resolved, "
        f"{stats['from_seed']} from seed, {stats['from_cache']} from cache, "
        f"{stats['probed']} probed"
    )
    for slug, platform in boards:
        print(f"  {platform:12} {slug}")


if __name__ == "__main__":
    _main()
