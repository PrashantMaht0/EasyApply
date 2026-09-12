"""A1 Resume Profiler. Turns the form and resume into a structured profile."""

import json

from strands import Agent

from core.llm import delimit, get_model, model_id_of, run_json
from agents.prompts import load
from core.filters import LEVELS

SYSTEM = str(load("a1_profiler"))

SPEC = {"skills": list, "seniority": str, "titles": list}


def build_profile(desired_role: str, level: str, work_mode: str, location: str,
                  extra_context: str, resume_text: str, guard=None) -> dict | None:
    """Returns the A1 profile, or None when the output failed the V4 schema guard twice."""
    model = get_model()
    make_agent = lambda: Agent(name="a1_profiler", model=model, system_prompt=SYSTEM,
                               callback_handler=None)
    prompt = f"""Intake form:
- desired_role: {desired_role}
- level: {level}
- work_mode: {work_mode}
- location: {location}

{delimit(extra_context, "extra_context")}

{delimit(resume_text, "resume")}

Return the profile JSON."""
    profile = run_json(make_agent, prompt, SPEC, model_id_of(model), guard)
    # seniority feeds the level filter, so anything outside the known set falls back to the form
    if profile is not None and profile.get("seniority") not in LEVELS:
        profile["seniority"] = level
    return profile


def profile_json(profile: dict) -> str:
    return json.dumps(profile, ensure_ascii=False)
