"""Offline checks that each platform normaliser produces a clean, correctly identified Posting."""

import json
import re

import pytest

from sources.ats import normalise_ashby, normalise_greenhouse, normalise_lever

CASES = [
    ("data/fixtures/greenhouse_stripe.json", normalise_greenhouse, "stripe", "gh_"),
    ("data/fixtures/lever_leverdemo.json", normalise_lever, "leverdemo", "lv_"),
    ("data/fixtures/ashby_ramp.json", normalise_ashby, "ramp", "ab_"),
]


@pytest.mark.parametrize("path,normalise,slug,prefix", CASES)
def test_normalise(path, normalise, slug, prefix):
    postings = normalise(slug, json.load(open(path)))
    assert postings
    for p in postings:
        assert p.posting_id.startswith(prefix)
        assert p.company_slug == slug
        assert p.title and p.url and p.description
        assert p.payload_hash
        # description is the verification source, so no markup may survive into storage
        assert not re.search(r"<[a-zA-Z/][^>]*>", p.description)
        assert "&lt;" not in p.description and "&amp;" not in p.description
