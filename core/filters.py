"""Hard constraints from the intake form. Plain Python, no model, runs before any inference."""

import re
import unicodedata

from core.geo import (CITY_COUNTRY, COUNTRY_ALIASES, COUNTRY_REGIONS, GLOBAL_WORDS,
                      REGION_WORDS, US_STATE_CODES)
from core.models import Posting

LEVELS = ["student", "junior", "mid", "senior", "staff"]

_LEVEL_PATTERNS = [
    ("student", r"\b(intern|internship|co-?op|new ?grad|graduate program|apprentice|working student)\b"),
    ("staff", r"\b(staff|principal|distinguished|fellow|director|head of|vp|chief)\b"),
    ("senior", r"\b(senior|sr\.?|lead|iii|iv)\b"),
    ("junior", r"\b(junior|jr\.?|entry[- ]level|associate|i{1,2}\b)\b"),
]
REMOTE = re.compile(r"\bremote\b", re.I)
_WORD = re.compile(r"[a-z0-9+#]+")
_STOPWORDS = {"and", "the", "for", "with", "of", "in", "at", "an", "a", "job", "role", "any"}
# short but highly discriminative, so the length cut must not throw them away
_SHORT_TERMS = {"ai", "ml", "qa", "ux", "ui", "sre", "nlp", "llm", "cv", "go", "bi", "ci"}
# a title written one way should still match a role written another way
_ALIAS_GROUPS = [
    {"ai", "artificial intelligence", "machine learning", "ml", "genai", "llm",
     "deep learning", "nlp"},
    {"sre", "site reliability", "reliability engineer"},
    {"devops", "platform engineer", "infrastructure engineer", "cloud engineer"},
    {"data engineer", "data engineering", "analytics engineer"},
    {"data scientist", "data science"},
    {"frontend", "front end", "front-end"},
    {"backend", "back end", "back-end"},
    {"fullstack", "full stack", "full-stack"},
    {"security engineer", "appsec", "infosec", "application security"},
    {"qa", "quality assurance", "sdet", "test engineer"},
    {"mobile", "ios", "android"},
]
# the kind of job, as opposed to the subject. "AI Engineer" and "AI Account Executive" share a
# subject but are different jobs, and matching on the subject alone let sales roles through.
_FAMILY_GROUPS = [
    {"engineer", "engineering", "developer", "programmer"},
    {"scientist", "science", "researcher", "research"},
    {"architect"},
    {"analyst", "analytics"},
    {"designer", "design"},
    {"manager", "management"},
]
# too common to discriminate, "engineer" alone would match most of a large board
_GENERIC = {"engineer", "engineering", "developer", "dev", "manager", "analyst", "specialist",
            "consultant", "lead", "senior", "junior", "staff", "principal", "intern", "software"}


def posting_level(title: str) -> str | None:
    """Seniority inferred from the title. None when the title says nothing about it."""
    lowered = title.lower()
    for level, pattern in _LEVEL_PATTERNS:
        if re.search(pattern, lowered):
            return level
    return None


# What each candidate level is willing to look at. Everyone can see below their level, but an
# internship only makes sense for a student or a junior.
_LEVEL_WINDOW = {
    "student": {"student", "junior"},
    "junior": {"student", "junior", "mid"},
    "mid": {"junior", "mid", "senior"},
    "senior": {"junior", "mid", "senior", "staff"},
    "staff": {"mid", "senior", "staff"},
}


def level_ok(desired: str, title: str) -> bool:
    """An unstated level passes, since most titles do not state one."""
    found = posting_level(title)
    if found is None or desired not in LEVELS:
        return True
    return found in _LEVEL_WINDOW[desired]


def work_mode_ok(desired: str, posting: Posting) -> bool:
    """An unknown posting mode passes, since most boards do not publish one."""
    if desired in ("any", "", None) or posting.work_mode is None:
        return True
    return posting.work_mode == desired


def needles(desired_role: str) -> set[str]:
    """Terms and phrases a title must carry, from the stated role and the curated alias groups."""
    role = (desired_role or "").lower().strip()
    terms = {w for w in _WORD.findall(role)
             if (len(w) > 2 or w in _SHORT_TERMS) and w not in _STOPWORDS}
    specific = terms - _GENERIC
    # every word generic: demand the whole role as one phrase rather than matching "engineer"
    found = set(specific) if specific else ({role} if role else set())
    for group in _ALIAS_GROUPS:
        if any(re.search(rf"\b{re.escape(member)}\b", role) for member in group):
            found |= group
    return found


def family(desired_role: str) -> set[str]:
    """The job family named in the role, if any. Empty when the role does not state one."""
    lowered = (desired_role or "").lower()
    found = set()
    for group in _FAMILY_GROUPS:
        if any(re.search(rf"\b{re.escape(member)}\b", lowered) for member in group):
            found |= group
    return found


def title_ok(title: str, terms: set[str], families: set[str] | None = None) -> bool:
    """The title must name the subject, and the job family when the role stated one."""
    lowered = title.lower()
    if terms and not any(re.search(rf"\b{re.escape(t)}\b", lowered) for t in terms):
        return False
    if families and not any(re.search(rf"\b{re.escape(f)}\b", lowered) for f in families):
        return False
    return True


def _fold(text: str) -> str:
    """Accents folded, so "São Paulo" matches the table entry for "sao paulo"."""
    stripped = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in stripped if not unicodedata.combining(c)).lower()


def _places(text: str) -> set[str]:
    """Countries and regions named anywhere in a location string."""
    lowered = _fold(text)
    found = set()
    for phrase, country in CITY_COUNTRY.items():
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            found.add(country)
    for alias, country in COUNTRY_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            found.add(country)
    for region in REGION_WORDS:
        if re.search(rf"\b{re.escape(region)}\b", lowered):
            found.add(region)
    # uppercase only, so the word "or" is never read as Oregon
    for code in re.findall(r"\b[A-Z]{2}\b", text or ""):
        if code in US_STATE_CODES:
            found.add("united states")
    return found


def user_geography(location: str) -> set[str]:
    """The countries and regions a candidate at this location can work in."""
    places = _places(location)
    reachable = set(places)
    for place in places:
        reachable |= COUNTRY_REGIONS.get(place, set())
    return reachable


def remote_scope(posting: Posting) -> set[str] | None:
    """Geography a remote posting is limited to. None means it is not scoped at all."""
    text = posting.location or ""
    if not REMOTE.search(text) and posting.work_mode != "remote":
        return set()
    if any(word in text.lower() for word in GLOBAL_WORDS):
        return None
    scope = _places(text)
    return scope or None


def desired_cities(desired: str) -> set[str]:
    """City names the candidate actually typed, so Galway is not satisfied by Dublin."""
    lowered = _fold(desired)
    return {city for city in CITY_COUNTRY
            if re.search(rf"\b{re.escape(city)}\b", lowered)}


def in_city(cities: set[str], posting: Posting) -> bool:
    lowered = _fold(posting.location or "")
    return any(re.search(rf"\b{re.escape(city)}\b", lowered) for city in cities)


def location_rank(desired: str, posting: Posting) -> int:
    """0 same city, 1 same country, 2 same region, 3 unscoped remote, 4 elsewhere."""
    if not desired:
        return 3
    wanted = user_geography(desired)
    countries = {c for c in wanted if c in COUNTRY_REGIONS}
    if not wanted or not posting.location:
        return 3
    cities = desired_cities(desired)
    if cities and in_city(cities, posting):
        return 0
    scope = remote_scope(posting)
    if scope is None:
        return 3
    places = scope or _places(posting.location)
    if places & countries:
        return 1
    reachable = set(places)
    for place in places:
        reachable |= COUNTRY_REGIONS.get(place, set())
    return 2 if reachable & wanted else 4


def location_ok(desired: str, posting: Posting) -> bool:
    """Region aware. A remote posting only skips geography when it names no geography."""
    if not desired or not posting.location:
        return True
    wanted = user_geography(desired)
    if not wanted:
        # the stated location is not in the table, so fall back to plain token overlap
        terms = {w for w in _WORD.findall(desired.lower()) if w not in _STOPWORDS}
        return bool(terms & set(_WORD.findall(posting.location.lower())))
    scope = remote_scope(posting)
    if scope is None:
        return True
    if scope:
        return bool(scope & wanted)
    found = _places(posting.location)
    for place in set(found):
        found |= COUNTRY_REGIONS.get(place, set())
    # an unrecognised location cannot be ruled out, so it survives to the scorer
    return bool(found & wanted) if found else True


def apply_filters(postings: list[Posting], desired_role: str, level: str, work_mode: str,
                  location: str) -> tuple[list[Posting], dict[str, int]]:
    """Returns survivors plus a per rule drop count for the funnel display."""
    terms = needles(desired_role)
    families = family(desired_role)
    dropped = {"title": 0, "level": 0, "work_mode": 0, "location": 0}
    kept = []
    for p in postings:
        if not title_ok(p.title, terms, families):
            dropped["title"] += 1
        elif not level_ok(level, p.title):
            dropped["level"] += 1
        elif not work_mode_ok(work_mode, p):
            dropped["work_mode"] += 1
        elif not location_ok(location, p):
            dropped["location"] += 1
        else:
            kept.append(p)
    return kept, dropped
