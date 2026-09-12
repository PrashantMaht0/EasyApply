"""A0 root orchestrator. Registers every specialist as a tool and runs the one final inference."""

import os

from strands import Agent, tool
from strands.types.exceptions import MaxTokensReachedException

from agents.prompts import load
from core.verify import BudgetExceeded, parse_json, schema_guard

DEFAULT_BEDROCK_MODEL_ID = "global.anthropic.claude-sonnet-4-6"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL_ID = "llama3.2"
# Caps generation. A high cap lets a small model loop on repeated output instead of closing the JSON
MAX_TOKENS = 1024
SCORER_MAX_TOKENS = 4096


def get_model(tier: str = "default"):
    """Bedrock by default, ollama for local work with no AWS credentials.

    tier is "scorer" for the high volume stage, which uses the cheaper model.
    """
    provider = os.getenv("MODEL_PROVIDER", "bedrock").lower()
    if provider == "ollama":
        from strands.models.ollama import OllamaModel

        return OllamaModel(
            host=os.getenv("OLLAMA_HOST") or DEFAULT_OLLAMA_HOST,
            model_id=os.getenv("OLLAMA_MODEL_ID") or DEFAULT_OLLAMA_MODEL_ID,
            temperature=0,
            max_tokens=SCORER_MAX_TOKENS if tier == "scorer" else MAX_TOKENS,
            # ollama JSON mode, so a local model cannot wrap the object in prose
            additional_args={"format": "json"},
        )
    if provider != "bedrock":
        raise ValueError(f"MODEL_PROVIDER must be bedrock or ollama, got {provider!r}")

    from strands.models import BedrockModel

    default_id = os.getenv("BEDROCK_MODEL_ID") or DEFAULT_BEDROCK_MODEL_ID
    model_id = os.getenv("BEDROCK_SCORER_MODEL_ID") or default_id if tier == "scorer" else default_id
    return BedrockModel(
        model_id=model_id,
        region_name=os.getenv("AWS_REGION") or "us-east-1",
        temperature=0,
        max_tokens=SCORER_MAX_TOKENS if tier == "scorer" else MAX_TOKENS,
    )


def model_id_of(model) -> str:
    """The id the provider actually resolved, used for cost accounting."""
    return model.get_config().get("model_id", "")


def _usage(source) -> tuple[int, int, float]:
    """Tokens and wall time from a result, or from the agent itself after a capped call."""
    try:
        metrics = getattr(source, "metrics", None) or source.event_loop_metrics
        summary = metrics.get_summary()
        usage = summary.get("accumulated_usage", {})
        return (int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0)),
                float(summary.get("total_duration", 0.0)))
    except (AttributeError, TypeError, ValueError):
        return 0, 0, 0.0


JSON_REMINDER = (
    "\n\nYour previous reply was not valid JSON matching the required shape. "
    "Reply with the JSON object only, no prose and no code fence."
)


def run_json(make_agent, prompt: str, spec: dict, model_id: str, guard=None):
    """Calls an agent and returns parsed JSON that passed V4. One retry, then None.

    make_agent is called per attempt, so a failed partial reply cannot pollute the retry.
    """
    for attempt in range(2):
        if guard is not None:
            guard.check()
        agent = make_agent()
        result, text = None, ""
        try:
            result = agent(prompt if attempt == 0 else prompt + JSON_REMINDER)
            text = str(result)
        except BudgetExceeded:
            raise
        except MaxTokensReachedException:
            # Strands raises rather than returning, so the partial reply is read back here
            text = last_assistant_text(agent)
        except Exception:
            text = ""
        if guard is not None:
            # usage stays on the agent, so a capped or failed attempt is charged like any other
            input_tokens, output_tokens, seconds = _usage(result or agent)
            guard.record(model_id, input_tokens, output_tokens,
                         agent=getattr(agent, "name", ""), seconds=seconds)
        parsed = parse_json(text)
        if parsed is not None and schema_guard(parsed, spec):
            return parsed
    return None


def last_assistant_text(agent) -> str:
    """The most recent assistant turn, including one cut short by the token cap."""
    for message in reversed(getattr(agent, "messages", [])):
        if message.get("role") != "assistant":
            continue
        return "".join(block.get("text", "") for block in message.get("content", []))
    return ""


def delimit(untrusted: str, label: str = "user_text") -> str:
    """Free text is data, never instructions. Wrapped so an injected order is visible as content."""
    cleaned = (untrusted or "").replace(f"</{label}>", "")
    return f"<{label}>\n{cleaned}\n</{label}>"


RANKING_SYSTEM = str(load("a0_ranking"))

RANKING_SPEC = {"rationale": str}


def ranking_rationale(ranked: list[dict], desired_role: str, guard=None) -> str:
    """A0 runs exactly one inference, here, over results that already passed every gate."""
    if not ranked:
        return ""
    lines = [
        f"{i + 1}. {r['fit_score']} {r['company']} {r['title']}"
        f" | {r['location'] or 'location not stated'}{' | hidden gem' if r['hidden_gem'] else ''}"
        f" | matched: {', '.join(c['skill'] for c in r['matched']) or 'none'}"
        f" | missing: {', '.join(c['skill'] for c in r['missing']) or 'none'}"
        for i, r in enumerate(ranked[:8])
    ]
    model = get_model()
    make_agent = lambda: Agent(name="a0_ranking", model=model, system_prompt=RANKING_SYSTEM,
                               callback_handler=None)
    prompt = f"Target role: {desired_role}\n\nShortlist, already in order:\n" + "\n".join(lines)
    parsed = run_json(make_agent, prompt, RANKING_SPEC, model_id_of(model), guard)
    return parsed["rationale"] if parsed else ""


def build_root_agent():
    """Registers A1 to A5 as tools for SDK structure and unified tracing.

    pipeline.py still decides the order. Nothing here chooses what happens next.
    Tools import their agents lazily, because those modules import this one.
    """

    @tool
    def profile_candidate(desired_role: str, level: str, work_mode: str, location: str,
                          extra_context: str, resume_text: str) -> dict:
        """A1. Extract a structured candidate profile from the intake form and resume."""
        from agents.profiler import build_profile

        return build_profile(desired_role, level, work_mode, location, extra_context,
                             resume_text) or {}

    @tool
    def source_companies(profile: dict, desired_role: str, location: str,
                         work_mode: str) -> list:
        """A2. Propose employers likely to be hiring for this profile."""
        from agents.sourcer import propose_companies

        return propose_companies(profile, desired_role, location, work_mode)

    @tool
    def score_postings(posting_ids: list, resume_text: str) -> list:
        """A3. Score a batch of stored postings against the resume."""
        import asyncio

        from agents.scorer import score_all
        from core.store import connect
        from pipeline import postings_from_rows

        conn = connect()
        marks = ",".join("?" * len(posting_ids))
        rows = conn.execute(
            f"SELECT * FROM postings WHERE posting_id IN ({marks})", posting_ids).fetchall()
        conn.close()
        proposals, _ = asyncio.run(score_all(postings_from_rows(rows), resume_text))
        return proposals

    @tool
    def synthesise_gaps(run_id: str) -> dict:
        """A4. Find gaps across every verified score in a run."""
        from agents.synthesist import analyse_gaps
        from core.store import connect, load_scores

        conn = connect()
        scores = load_scores(conn, run_id)
        conn.close()
        return analyse_gaps(scores) or {}

    @tool
    def tailor_resume(posting_id: str) -> dict:
        """A5. Draft tailored resume bullets for one scored posting."""
        from agents.tailor import tailor_rewrites
        from core.store import connect, load_tailoring_inputs

        conn = connect()
        inputs = load_tailoring_inputs(conn, posting_id)
        conn.close()
        if inputs is None:
            return {}
        row, matched, missing, profile = inputs
        return tailor_rewrites(row["title"], row["description"], profile.resume_text,
                               matched, missing) or {}

    return Agent(
        name="a0_orchestrator",
        model=get_model(),
        system_prompt=RANKING_SYSTEM,
        tools=[profile_candidate, source_companies, score_postings, synthesise_gaps,
               tailor_resume],
        callback_handler=None,
    )
