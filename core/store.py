"""SQLite blackboard. The single place shared state lives."""

import json
import os
import sqlite3
from datetime import datetime, timezone

from core.models import Posting, Profile, Score

DEFAULT_DB = "data/easeapply.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
  id INTEGER PRIMARY KEY,
  desired_role TEXT,
  level TEXT,
  work_mode TEXT,
  location TEXT,
  extra_context TEXT,
  resume_text TEXT,
  profile_json TEXT,
  boards_refreshed_at TEXT
);

CREATE TABLE IF NOT EXISTS companies (
  slug TEXT,
  platform TEXT,
  name TEXT,
  resolved_at TEXT,
  active INTEGER,
  PRIMARY KEY (slug, platform)
);

CREATE TABLE IF NOT EXISTS postings (
  posting_id TEXT PRIMARY KEY,
  company_slug TEXT,
  platform TEXT,
  title TEXT,
  location TEXT,
  work_mode TEXT,
  url TEXT,
  description TEXT,
  posted_at TEXT,
  first_seen TEXT,
  fetched_at TEXT,
  syndicated INTEGER,
  payload_hash TEXT
);

CREATE TABLE IF NOT EXISTS scores (
  posting_id TEXT,
  run_id TEXT,
  fit_score INTEGER,
  matched TEXT,
  missing TEXT,
  sponsorship TEXT,
  verified_ratio REAL,
  PRIMARY KEY (posting_id, run_id)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY,
  kind TEXT,
  started_at TEXT,
  funnel TEXT,
  new_postings INTEGER,
  tokens INTEGER,
  cost_usd REAL,
  emailed INTEGER
);
"""


def now() -> str:
    """UTC timestamp in ISO 8601, used for every stored time."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> str:
    return os.getenv("EASEAPPLY_DB") or DEFAULT_DB


def connect() -> sqlite3.Connection:
    path = db_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def pull_blackboard() -> bool:
    """Fetch the blackboard from S3 before a run. An AgentCore session starts with a blank disk."""
    bucket = os.getenv("EASEAPPLY_S3_BUCKET")
    if not bucket:
        return False
    import boto3
    from botocore.exceptions import ClientError

    path = db_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        boto3.client("s3").download_file(bucket, _blackboard_key(), path)
        return True
    except ClientError:
        # no object yet, so this is the first run and init_db creates an empty blackboard
        return False


def prune_descriptions() -> int:
    """Every run refetches descriptions, so only the scored ones are worth carrying to S3."""
    conn = connect()
    pruned = conn.execute(
        "UPDATE postings SET description = '' "
        "WHERE description != '' AND posting_id NOT IN (SELECT posting_id FROM scores)").rowcount
    conn.commit()
    # VACUUM refuses to run inside a transaction, and it is what actually shrinks the file
    conn.isolation_level = None
    conn.execute("VACUUM")
    conn.close()
    return pruned


def push_blackboard() -> bool:
    """Write the blackboard back to S3 after a run, before the session is discarded."""
    bucket = os.getenv("EASEAPPLY_S3_BUCKET")
    if not bucket or not os.path.exists(db_path()):
        return False
    import boto3

    # the whole file goes over the wire, so drop what the next run fetches again anyway
    prune_descriptions()
    boto3.client("s3").upload_file(db_path(), bucket, _blackboard_key())
    return True


def _blackboard_key() -> str:
    return os.getenv("EASEAPPLY_S3_KEY") or "easeapply.db"


def upsert_postings(conn: sqlite3.Connection, postings: list[Posting]) -> int:
    """Insert or refresh postings. first_seen is written once and never updated."""
    stamp = now()
    rows = [
        (
            p.posting_id, p.company_slug, p.platform, p.title, p.location,
            p.work_mode, p.url, p.description, p.posted_at, stamp, stamp,
            p.syndicated, p.payload_hash,
        )
        for p in postings
    ]
    conn.executemany(
        """
        INSERT INTO postings (
          posting_id, company_slug, platform, title, location, work_mode, url,
          description, posted_at, first_seen, fetched_at, syndicated, payload_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(posting_id) DO UPDATE SET
          title = excluded.title,
          location = excluded.location,
          work_mode = excluded.work_mode,
          url = excluded.url,
          description = excluded.description,
          posted_at = excluded.posted_at,
          fetched_at = excluded.fetched_at,
          payload_hash = excluded.payload_hash
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def set_syndicated(conn: sqlite3.Connection, values: dict[str, int | None]) -> None:
    """NULL means the check failed and must never be read as 'not syndicated'."""
    conn.executemany(
        "UPDATE postings SET syndicated = ? WHERE posting_id = ?",
        [(v, k) for k, v in values.items()],
    )
    conn.commit()


def upsert_company(
    conn: sqlite3.Connection, slug: str, platform: str, name: str, active: int
) -> None:
    """Caches probe misses as active = 0 so a dead slug is not retried every run."""
    conn.execute(
        """
        INSERT INTO companies (slug, platform, name, resolved_at, active)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(slug, platform) DO UPDATE SET
          name = excluded.name,
          resolved_at = excluded.resolved_at,
          active = excluded.active
        """,
        (slug, platform, name, now(), active),
    )
    conn.commit()


def cached_probes(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Every slug already probed, hits and misses alike."""
    cur = conn.execute("SELECT slug, platform, active FROM companies")
    return {(r["slug"], r["platform"]): r["active"] for r in cur}


def save_profile(conn: sqlite3.Connection, profile: Profile) -> None:
    """Singleton row. resume_text is a verification source and is stored verbatim."""
    conn.execute(
        """
        INSERT INTO profile (
          id, desired_role, level, work_mode, location, extra_context,
          resume_text, profile_json, boards_refreshed_at
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          desired_role = excluded.desired_role,
          level = excluded.level,
          work_mode = excluded.work_mode,
          location = excluded.location,
          extra_context = excluded.extra_context,
          resume_text = excluded.resume_text,
          profile_json = excluded.profile_json,
          boards_refreshed_at = excluded.boards_refreshed_at
        """,
        (profile.desired_role, profile.level, profile.work_mode, profile.location,
         profile.extra_context, profile.resume_text, profile.profile_json,
         profile.boards_refreshed_at),
    )
    conn.commit()


def load_profile(conn: sqlite3.Connection) -> Profile | None:
    row = conn.execute("SELECT * FROM profile WHERE id = 1").fetchone()
    if row is None:
        return None
    return Profile(
        desired_role=row["desired_role"], level=row["level"], work_mode=row["work_mode"],
        location=row["location"], extra_context=row["extra_context"] or "",
        resume_text=row["resume_text"] or "", profile_json=row["profile_json"] or "",
        boards_refreshed_at=row["boards_refreshed_at"],
    )


def save_scores(conn: sqlite3.Connection, scores: list[Score]) -> None:
    """Only reached by rows that already passed the full verification gate."""
    conn.executemany(
        """
        INSERT INTO scores (posting_id, run_id, fit_score, matched, missing, sponsorship, verified_ratio)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(posting_id, run_id) DO UPDATE SET
          fit_score = excluded.fit_score,
          matched = excluded.matched,
          missing = excluded.missing,
          sponsorship = excluded.sponsorship,
          verified_ratio = excluded.verified_ratio
        """,
        [(s.posting_id, s.run_id, s.fit_score, json.dumps(s.matched), json.dumps(s.missing),
          json.dumps(s.sponsorship), s.verified_ratio) for s in scores],
    )
    conn.commit()


def save_run(conn: sqlite3.Connection, run_id: str, kind: str, funnel: dict,
             new_postings: int, tokens: int, cost_usd: float, emailed: int = 0) -> None:
    conn.execute(
        """
        INSERT INTO runs (run_id, kind, started_at, funnel, new_postings, tokens, cost_usd, emailed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id) DO UPDATE SET
          funnel = excluded.funnel,
          new_postings = excluded.new_postings,
          tokens = excluded.tokens,
          cost_usd = excluded.cost_usd,
          emailed = excluded.emailed
        """,
        (run_id, kind, now(), json.dumps(funnel), new_postings, tokens, cost_usd, emailed),
    )
    conn.commit()


def load_scores(conn: sqlite3.Connection, run_id: str) -> list[Score]:
    rows = conn.execute("SELECT * FROM scores WHERE run_id = ?", (run_id,)).fetchall()
    return [
        Score(posting_id=r["posting_id"], run_id=r["run_id"], fit_score=r["fit_score"],
              matched=json.loads(r["matched"] or "[]"), missing=json.loads(r["missing"] or "[]"),
              sponsorship=json.loads(r["sponsorship"] or "{}"),
              verified_ratio=r["verified_ratio"] or 0.0)
        for r in rows
    ]


def load_tailoring_inputs(conn: sqlite3.Connection, posting_id: str):
    """The posting, its latest verified claims and the profile, or None when any is missing."""
    row = conn.execute("SELECT * FROM postings WHERE posting_id = ?", (posting_id,)).fetchone()
    score = conn.execute(
        "SELECT matched, missing FROM scores WHERE posting_id = ? ORDER BY rowid DESC LIMIT 1",
        (posting_id,)).fetchone()
    profile = load_profile(conn)
    if row is None or profile is None:
        return None
    matched = json.loads(score["matched"]) if score else []
    missing = json.loads(score["missing"]) if score else []
    return row, matched, missing, profile


def company_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Display names for board slugs, so the table shows a company rather than its URL slug."""
    rows = conn.execute("SELECT slug, name FROM companies WHERE active = 1 AND name IS NOT NULL")
    return {r["slug"]: r["name"] for r in rows}


def cumulative_spend(conn: sqlite3.Connection) -> tuple[float, int]:
    """Total spend and run count across every run, printed at startup."""
    row = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) c, COUNT(*) n FROM runs").fetchone()
    return float(row["c"]), int(row["n"])


def active_boards(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Boards resolved by an earlier run. The digest uses these and skips A2 entirely."""
    cur = conn.execute(
        "SELECT slug, platform FROM companies WHERE active = 1 ORDER BY slug")
    return [(r["slug"], r["platform"]) for r in cur]


def unseen_posting_ids(conn: sqlite3.Connection, posting_ids: list[str]) -> set[str]:
    """IDs with no row yet. This is the diff the digest is built on."""
    known = set()
    for start in range(0, len(posting_ids), 500):
        chunk = posting_ids[start:start + 500]
        marks = ",".join("?" * len(chunk))
        cur = conn.execute(
            f"SELECT posting_id FROM postings WHERE posting_id IN ({marks})", chunk)
        known.update(r["posting_id"] for r in cur)
    return set(posting_ids) - known


def latest_run(conn: sqlite3.Connection, kinds: tuple[str, ...] = ("full", "demo")):
    """Most recent run of the given kinds, used by demo mode to replay a stored result."""
    marks = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT * FROM runs WHERE kind IN ({marks}) ORDER BY started_at DESC LIMIT 1", kinds
    ).fetchone()
