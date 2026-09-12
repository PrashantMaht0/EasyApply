"""A4 Gap Synthesist. Code counts the gaps across the shortlist, the model only explains them."""

import re
from collections import defaultdict

from strands import Agent

from core.llm import get_model, model_id_of, run_json
from agents.prompts import load

SYSTEM = str(load("a4_synthesist"))

SPEC = {"notes": list}
# a gap only matters on a role worth applying to, weak matches would drown it in noise
MIN_FIT_FOR_GAPS = 60
MAX_GAPS = 6
# a job title or seniority level written into the skill field is not a gap anyone can close
_NOT_A_SKILL = re.compile(r"\b(engineer|developer|scientist|manager|architect|analyst|designer|"
                          r"senior|junior|staff|principal|intern)\b", re.I)


def _short(skill: str) -> str:
    """Guards the prompt size when a model writes a sentence into the skill field."""
    words = (skill or "").split()
    return " ".join(words[:4])


def _top(scores: list, field: str, companies: dict[str, str]) -> list[tuple[str, int]]:
    """Skills counted across worthwhile roles, one vote per employer. Counts come from code."""
    voters, names = defaultdict(set), {}
    for s in scores:
        if s.fit_score < MIN_FIT_FOR_GAPS:
            continue
        employer = companies.get(s.posting_id, s.posting_id)
        for claim in getattr(s, field):
            name = _short(claim["skill"])
            if _NOT_A_SKILL.search(name):
                continue
            voters[name.lower()].add(employer)
            names.setdefault(name.lower(), name)
    ranked = sorted(voters.items(), key=lambda item: (-len(item[1]), item[0]))[:MAX_GAPS]
    return [(names[key], len(employers)) for key, employers in ranked]


def analyse_gaps(scores: list, guard=None, companies: dict[str, str] | None = None) -> dict | None:
    """Gaps and strengths come from code. The model adds one note per gap and short advice."""
    companies = companies or {}
    gaps = _top(scores, "missing", companies)
    if not gaps:
        return None
    strengths = [name for name, _ in _top(scores, "matched", companies)]
    listing = "\n".join(f"{i + 1}. {name}, asked for by {n} employers"
                         for i, (name, n) in enumerate(gaps))
    model = get_model()
    make_agent = lambda: Agent(name="a4_synthesist", model=model, system_prompt=SYSTEM,
                               callback_handler=None)
    prompt = (f"Counted gaps:\n{listing}\n\nStrengths: {', '.join(strengths) or 'none'}\n\n"
              "Return the JSON object.")
    parsed = run_json(make_agent, prompt, SPEC, model_id_of(model), guard) or {}
    notes = [n for n in parsed.get("notes", []) if isinstance(n, str)]
    advice = parsed.get("advice")
    return {
        "gaps": [{"skill": name, "employers": n, "note": notes[i] if i < len(notes) else ""}
                 for i, (name, n) in enumerate(gaps)],
        "strengths": strengths,
        "advice": advice if isinstance(advice, str) else "",
    }
