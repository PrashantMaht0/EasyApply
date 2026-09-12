"""Hard constraints run before any inference, so a wrong predicate silently costs matches."""

from core.filters import (apply_filters, family, level_ok, location_ok, location_rank,
                          needles, title_ok, work_mode_ok)
from core.models import Posting


def _posting(title="Backend Engineer", location="Berlin", work_mode=None):
    return Posting(posting_id="gh_1", company_slug="acme", platform="greenhouse", title=title,
                   location=location, url="u", description="d", work_mode=work_mode)


def test_level_allows_adjacent_and_unstated():
    assert level_ok("mid", "Senior Backend Engineer")
    assert level_ok("mid", "Backend Engineer")
    assert not level_ok("mid", "Backend Engineering Intern")
    assert not level_ok("junior", "Principal Engineer")


def test_unknown_work_mode_passes():
    assert work_mode_ok("remote", _posting(work_mode=None))
    assert not work_mode_ok("remote", _posting(work_mode="onsite"))


def test_remote_only_skips_geography_when_it_names_none():
    """A US role flagged remote is not reachable from Ireland, which is what broke live runs."""
    ireland = "Athlone, Ireland"
    assert not location_ok(ireland, _posting(location="New York, NY (HQ)", work_mode="remote"))
    assert not location_ok(ireland, _posting(location="Remote - United States"))
    assert location_ok(ireland, _posting(location="Remote (EMEA)"))
    assert location_ok(ireland, _posting(location="Remote"))
    assert location_ok(ireland, _posting(location="Cork, Ireland"))
    assert not location_ok(ireland, _posting(location="Toronto"))
    # these all reached a live shortlist for an Ireland based candidate before the geo table grew
    for foreign in ("China", "Seoul, South Korea", "S\u00e3o Paulo", "Gurugram", "Ontario - Remote"):
        assert not location_ok(ireland, _posting(location=foreign)), foreign
    for reachable in ("Remote - Ireland", "Paris, France", "London, UK", "PL-Warsaw-Lixa C"):
        assert location_ok(ireland, _posting(location=reachable)), reachable


def test_short_and_aliased_role_terms_match():
    """AI Engineer used to collapse to "engineer" and match every engineering role."""
    terms = needles("AI Engineer")
    assert "ai" in terms and "engineer" not in terms
    assert title_ok("Machine Learning Engineer", terms)
    assert title_ok("Senior AI Engineer", terms)
    assert not title_ok("Backend Engineer", terms)
    # the reverse direction used to return nothing at all
    assert title_ok("ML Engineer", needles("Machine Learning Engineer"))


def test_subject_alone_does_not_qualify_a_different_job():
    """"AI Engineer" must not match AI sales or AI product roles, which reached a live run."""
    terms, families = needles("AI Engineer"), family("AI Engineer")
    assert title_ok("Applied AI Engineer", terms, families)
    assert not title_ok("Enterprise Account Executive, AI Natives", terms, families)
    assert not title_ok("Product Manager II, AI & Data Security", terms, families)


def test_apply_filters_counts_drops_by_rule():
    postings = [_posting(), _posting(title="Marketing Manager"),
                _posting(title="Backend Engineering Intern")]
    kept, dropped = apply_filters(postings, "backend engineer", "mid", "any", "Berlin, Germany")
    assert [p.title for p in kept] == ["Backend Engineer"]
    assert dropped["title"] == 1 and dropped["level"] == 1


def test_location_rank_orders_city_then_country_then_region():
    """Searching Galway must not treat a Dublin role as if it were local."""
    galway = "Galway, Ireland"
    assert location_rank(galway, _posting(location="Galway, Ireland")) == 0
    assert location_rank(galway, _posting(location="Dublin, Ireland")) == 1
    assert location_rank(galway, _posting(location="Remote - Ireland")) == 1
    assert location_rank(galway, _posting(location="Paris, France")) == 2
    assert location_rank(galway, _posting(location="Remote")) == 3
    assert location_rank(galway, _posting(location="Tokyo, Japan")) == 4


def test_level_window_widens_downward_but_not_to_internships():
    """A senior should see junior roles, never internships. A junior may see both."""
    assert level_ok("senior", "Junior Engineer")
    assert not level_ok("senior", "Engineering Intern")
    assert level_ok("junior", "Engineering Intern")
    assert level_ok("staff", "Senior Engineer")
    assert not level_ok("staff", "Junior Engineer")


def test_generic_role_is_matched_as_one_phrase():
    """A role of only generic words must not collapse to "engineer" and match every role."""
    terms = needles("Software Engineer")
    assert title_ok("Senior Software Engineer, Payments", terms)
    assert not title_ok("Hardware Engineer", terms)
