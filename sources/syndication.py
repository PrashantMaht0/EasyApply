"""Open job API cross check. Answers whether a posting already leaked to the open market."""

import asyncio
import json
import re

import httpx

from core.models import Posting
from sources.ats import TIMEOUT, USER_AGENT

ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api?page={page}"
# Bounded rather than exhaustive, the index only has to be wide enough to catch syndication
ARBEITNOW_PAGES = 5
REMOTEOK_URL = "https://remoteok.com/api"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _key(company: str, title: str) -> str:
    return f"{_NON_ALNUM.sub('', company.lower())}|{_NON_ALNUM.sub('', title.lower())}"


async def _fetch(client: httpx.AsyncClient, url: str):
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        return None


async def build_index() -> set[str] | None:
    """Company plus title keys seen on the open market. None when every source failed."""
    urls = [ARBEITNOW_URL.format(page=n) for n in range(1, ARBEITNOW_PAGES + 1)]
    async with httpx.AsyncClient(
        timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        pages = await asyncio.gather(*(_fetch(client, u) for u in urls))
        remoteok = await _fetch(client, REMOTEOK_URL)
    if remoteok is None and all(page is None for page in pages):
        return None
    index = set()
    for page in pages:
        for job in (page or {}).get("data", []):
            index.add(_key(job.get("company_name", ""), job.get("title", "")))
    # RemoteOK returns its legal notice as the first element, which is not a posting
    for job in (remoteok or [])[1:]:
        index.add(_key(job.get("company", ""), job.get("position", "")))
    return index


async def check(postings: list[Posting]) -> dict[str, int | None]:
    """1 syndicated, 0 not found, None when the check itself failed."""
    index = await build_index()
    if index is None:
        return {p.posting_id: None for p in postings}
    return {
        p.posting_id: 1 if _key(p.company_slug, p.title) in index else 0
        for p in postings
    }


def _main() -> None:
    index = asyncio.run(build_index())
    if index is None:
        print("both syndication sources failed, every posting stays NULL")
        raise SystemExit(1)
    print(f"{len(index)} open market postings indexed")
    for sample in list(index)[:3]:
        print(f"  {sample}")


if __name__ == "__main__":
    _main()
