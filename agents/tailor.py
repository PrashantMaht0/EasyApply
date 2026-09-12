"""A5 Tailoring Agent. Runs on demand for one job, grounded in verified quotes."""

import re

from strands import Agent

from core.llm import delimit, get_model, model_id_of, run_json
from agents.prompts import load
from core.verify import normalise_span, span_present

SYSTEM = str(load("a5_tailor"))

SPEC = {"rewrites": list}


_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9+#./]{1,}\b")
# capitalised words that carry no product meaning, so they are never treated as invented tools
_HARMLESS = {"i", "a", "an", "the", "built", "used", "ran", "led", "designed", "developed",
             "created", "managed", "implemented", "integrated", "delivered", "owned", "wrote",
             "and", "for", "with", "on", "to", "of", "in", "at", "by"}


def invented_terms(rewritten: str, resume_text: str) -> list[str]:
    """Product and tool names in the rewrite that the resume never mentions."""
    haystack = normalise_span(resume_text)
    found = []
    for match in _PROPER.finditer(rewritten):
        word = match.group(0)
        if match.start() == 0 or word.lower() in _HARMLESS:
            continue
        if normalise_span(word) in haystack:
            continue
        found.append(word)
    return list(dict.fromkeys(found))


def verify_rewrites(rewrites: list[dict], resume_text: str,
                    missing: list[dict]) -> tuple[list[dict], int]:
    """Keeps rewrites anchored to a real resume line, and flags any that claim a missing skill."""
    gaps = [c["skill"] for c in missing if c.get("skill")]
    kept, dropped = [], 0
    for item in rewrites:
        if not isinstance(item, dict) or not item.get("rewritten"):
            dropped += 1
            continue
        original = item.get("original", "")
        # the same discipline the scorer lives under, an unlocatable quote is not evidence
        if not span_present(original, resume_text):
            dropped += 1
            continue
        lowered = item["rewritten"].lower()
        kept.append({
            "original": original,
            "rewritten": item["rewritten"],
            "targets": item.get("targets", ""),
            "change": item.get("change", ""),
            "unsupported": [skill for skill in gaps if skill.lower() in lowered],
            "invented": invented_terms(item["rewritten"], resume_text),
        })
    return kept, dropped


def tailor_rewrites(title: str, job_text: str, resume_text: str,
                   matched: list[dict], missing: list[dict], guard=None) -> dict | None:
    """Rewrites for one posting. The evidence passed in has already passed V6."""
    evidence = "\n".join(
        f"- requires {c['skill']}: \"{c.get('job_span', '')}\"" for c in (matched + missing)[:12]
    )
    model = get_model()
    make_agent = lambda: Agent(name="a5_tailor", model=model, system_prompt=SYSTEM,
                               callback_handler=None)
    prompt = f"""Role: {title}

Verified requirements:
{evidence or "none"}

{delimit(job_text[:4000], "job")}

{delimit(resume_text[:6000], "resume")}

Write at most five bullets. Return the JSON object."""
    parsed = run_json(make_agent, prompt, SPEC, model_id_of(model), guard)
    if not parsed:
        return None
    rewrites, dropped = verify_rewrites(parsed["rewrites"], resume_text, missing)
    return {"rewrites": rewrites, "dropped": dropped}
