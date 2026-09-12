"""first_seen is written once. This invariant is what the daily digest diff depends on."""

from dataclasses import replace

from core.models import Posting
from core.store import connect, init_db, unseen_posting_ids, upsert_postings


def _posting(title: str) -> Posting:
    return Posting(
        posting_id="gh_1", company_slug="acme", platform="greenhouse", title=title,
        location="Remote", url="https://example.com/1", description="text",
        payload_hash="abc",
    )


def test_first_seen_survives_reupsert(tmp_path, monkeypatch):
    monkeypatch.setenv("EASEAPPLY_DB", str(tmp_path / "test.db"))
    init_db()
    conn = connect()

    upsert_postings(conn, [_posting("Engineer")])
    original = conn.execute("SELECT first_seen FROM postings").fetchone()["first_seen"]

    upsert_postings(conn, [replace(_posting("Engineer"), title="Senior Engineer")])
    row = conn.execute("SELECT first_seen, title FROM postings").fetchone()

    assert row["first_seen"] == original
    assert row["title"] == "Senior Engineer"
    conn.close()


def test_only_unseen_ids_reach_the_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("EASEAPPLY_DB", str(tmp_path / "digest.db"))
    init_db()
    conn = connect()
    upsert_postings(conn, [_posting("Engineer")])

    unseen = unseen_posting_ids(conn, ["gh_1", "gh_2"])

    assert unseen == {"gh_2"}
    conn.close()
