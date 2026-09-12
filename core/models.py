"""Typed objects that agents and code exchange. Mirrors the SQLite schema one to one."""

from dataclasses import dataclass, field


@dataclass
class Posting:
    """A single job posting normalised from any ATS platform."""

    posting_id: str
    company_slug: str
    platform: str
    title: str
    location: str
    url: str
    description: str
    work_mode: str | None = None
    posted_at: str | None = None
    first_seen: str | None = None
    fetched_at: str | None = None
    # 1 seen on an aggregator, 0 not seen, None the check itself failed
    syndicated: int | None = None
    payload_hash: str = ""


@dataclass
class Profile:
    """Singleton candidate profile, form answers plus the A1 output."""

    desired_role: str
    level: str
    work_mode: str
    location: str
    extra_context: str = ""
    # verbatim resume text, the source of truth for span verification
    resume_text: str = ""
    profile_json: str = ""
    boards_refreshed_at: str | None = None


@dataclass
class Score:
    """A verified fit score for one posting in one run."""

    posting_id: str
    run_id: str
    fit_score: int
    matched: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    sponsorship: dict = field(default_factory=dict)
    verified_ratio: float = 0.0
