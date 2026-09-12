"""A failed cross check must stay NULL. Collapsing it to 0 would manufacture a Hidden Gem."""

import asyncio

import sources.syndication as syndication
from core.models import Posting


def test_failed_sources_yield_none(monkeypatch):
    async def all_sources_fail():
        return None

    monkeypatch.setattr(syndication, "build_index", all_sources_fail)
    posting = Posting(
        posting_id="gh_1", company_slug="acme", platform="greenhouse", title="Engineer",
        location="Remote", url="https://example.com/1", description="text",
    )

    result = asyncio.run(syndication.check([posting]))

    assert result == {"gh_1": None}
