"""A2 Sourcing Strategist. Maps a profile to employers likely to be hiring that person."""

from strands import Agent

from core.llm import delimit, get_model, model_id_of, run_json
from agents.prompts import load

SYSTEM = str(load("a2_sourcer"))

SPEC = {"companies": list}
TARGET_COUNT = 40


def propose_companies(profile: dict, desired_role: str, location: str,
                      work_mode: str, guard=None, exclude: list[str] | None = None) -> list[str]:
    """Company names only. These are proposals, resolved against real boards by code next."""
    model = get_model()
    make_agent = lambda: Agent(name="a2_sourcer", model=model, system_prompt=SYSTEM,
                               callback_handler=None)
    prompt = f"""Candidate profile:
- desired_role: {desired_role}
- seniority: {profile.get('seniority')}
- skills: {', '.join(profile.get('skills', [])[:25])}
- domains: {', '.join(profile.get('domains', []))}
- location: {location}
- work_mode: {work_mode}

{delimit(", ".join(exclude or []), "known")}

Name {TARGET_COUNT} employers worth searching that are not in the known list. Return the JSON object."""
    parsed = run_json(make_agent, prompt, SPEC, model_id_of(model), guard)
    if not parsed:
        return []
    skip = {name.lower() for name in (exclude or [])}
    # the prompt asks for new employers, and code makes sure a known one never gets through
    names = [c.strip() for c in parsed["companies"]
             if isinstance(c, str) and c.strip() and c.strip().lower() not in skip]
    # small models repeat names, and a duplicate would probe the same board twice
    return list(dict.fromkeys(names))[:TARGET_COUNT]
