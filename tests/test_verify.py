"""The safety boundary. These checks are what stop a model output becoming a stored fact."""

from core.verify import hidden_gem, verify_scores

JOB = "We use Terraform daily and run event streaming with Kafka."
RESUME = "Managed Terraform modules across 3 AWS accounts."


def _gate(proposals, requested={"gh_1"}):
    return verify_scores(proposals, requested, {"gh_1": JOB}, RESUME, "run1")


def test_invented_posting_id_is_dropped():
    scores, stats = _gate([{"posting_id": "gh_9999", "fit_score": 90}])
    assert scores == []
    assert stats["unknown_ids_dropped"] == 1


def test_unquotable_claim_is_dropped_and_ratio_falls():
    scores, stats = _gate([{
        "posting_id": "gh_1", "fit_score": 80,
        "matched": [
            {"skill": "Terraform", "job_span": "We use Terraform daily",
             "resume_span": "Managed Terraform modules"},
            {"skill": "Rust", "job_span": "deep Rust experience required"},
        ],
        "missing": [],
    }])
    assert [c["skill"] for c in scores[0].matched] == ["Terraform"]
    assert scores[0].verified_ratio == 0.5
    assert stats["claims_verified"] == 1


def test_out_of_range_score_is_clamped():
    scores, stats = _gate([{"posting_id": "gh_1", "fit_score": 140}])
    assert scores[0].fit_score == 100
    assert stats["scores_clamped"] == 1


def test_unquoted_sponsorship_downgrades():
    scores, _ = _gate([{"posting_id": "gh_1", "fit_score": 50,
                        "sponsorship": {"status": "mentioned", "span": "we sponsor visas"}}])
    assert scores[0].sponsorship == {"status": "not_mentioned"}


def test_failed_syndication_check_cannot_be_a_hidden_gem():
    assert hidden_gem("2026-09-06T00:00:00+00:00", 0, 80) is True
    assert hidden_gem("2026-09-06T00:00:00+00:00", None, 80) is False
    assert hidden_gem("2026-09-06T00:00:00+00:00", 1, 80) is False


def test_tailoring_rewrite_must_quote_a_real_resume_line():
    """A rewrite that cannot point at a resume line is invention, so it is dropped."""
    from agents.tailor import verify_rewrites

    kept, dropped = verify_rewrites(
        [{"original": "Managed Terraform modules", "rewritten": "Owned Terraform modules",
          "targets": "Terraform", "change": "stronger verb"},
         {"original": "Led a team of 12 engineers", "rewritten": "Led 12 engineers",
          "targets": "leadership", "change": "shorter"}],
        RESUME, [])

    assert [k["original"] for k in kept] == ["Managed Terraform modules"]
    assert dropped == 1


def test_tailoring_flags_tools_the_resume_never_mentions():
    """Nova rewrote real experience onto Cloudflare products the resume does not contain."""
    from agents.tailor import invented_terms

    assert invented_terms(
        "Built agents on Workers with Durable Objects", RESUME) == ["Workers", "Durable", "Objects"]
    assert invented_terms("Managed Terraform modules across 3 AWS accounts", RESUME) == []
