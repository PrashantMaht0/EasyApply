"""A0. One inference over an already ranked shortlist, saying what to apply to first."""

from strands import Agent

from agents.prompts import load
from core.llm import get_model, model_id_of, run_json


RANKING_SYSTEM = str(load("a6_ranking"))

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
    make_agent = lambda: Agent(name="a6_ranking", model=model, system_prompt=RANKING_SYSTEM,
                               callback_handler=None)
    prompt = f"Target role: {desired_role}\n\nShortlist, already in order:\n" + "\n".join(lines)
    parsed = run_json(make_agent, prompt, RANKING_SPEC, model_id_of(model), guard)
    return parsed["rationale"] if parsed else ""
