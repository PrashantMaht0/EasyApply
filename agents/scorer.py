"""A3 Fit Scorer swarm. Batches of five postings scored concurrently, evidence required."""

import asyncio
import re

from strands import Agent

from agents.orchestrator import delimit, get_model, model_id_of, run_json
from agents.prompts import load
from core.models import Posting
from core.verify import BudgetExceeded

BATCH_SIZE = 5
MAX_DESCRIPTION_CHARS = 2500

SYSTEM = str(load("a3_scorer"))

# Where requirements usually begin. Many boards open with a long company introduction, and a
# plain head cut left the requirements outside the window for about half the postings.
_REQUIREMENTS = re.compile(
    r"requirements|qualifications|what you.ll (need|bring)|you (have|bring|will need)|about you|"
    r"who you are|must have|skills and experience|what we.re looking for|you should have", re.I)
# context kept above the heading, where the responsibilities usually sit
_LEAD_IN = 600

SPEC = {"results": list}


def relevant_text(description: str) -> str:
    """The part of a posting that says what the job needs, capped at MAX_DESCRIPTION_CHARS."""
    match = _REQUIREMENTS.search(description)
    if not match or match.start() < MAX_DESCRIPTION_CHARS - _LEAD_IN:
        return description[:MAX_DESCRIPTION_CHARS]
    start = match.start() - _LEAD_IN
    return description[start:start + MAX_DESCRIPTION_CHARS]


def batches(postings: list[Posting], size: int = BATCH_SIZE) -> list[list[Posting]]:
    return [postings[i:i + size] for i in range(0, len(postings), size)]


def _prompt(batch: list[Posting], resume_text: str) -> str:
    blocks = []
    for p in batch:
        posting = (f"title: {p.title}\ncompany: {p.company_slug}\n"
                   f"description:\n{relevant_text(p.description)}")
        # the id stays outside the fence, it is ours, everything inside came from a job board
        blocks.append(f"posting_id: {p.posting_id}\n{delimit(posting, 'job')}")
    joined = "\n\n---\n\n".join(blocks)
    return f"""{delimit(resume_text[:MAX_DESCRIPTION_CHARS * 2], 'resume')}

Postings:
{joined}

Score all {len(batch)} postings. Return the JSON object."""


def score_batch(batch: list[Posting], resume_text: str, guard=None) -> list[dict]:
    """One batch. Returns raw proposals, which are not facts until the gate accepts them."""
    model = get_model("scorer")
    make_agent = lambda: Agent(name="a3_scorer", model=model, system_prompt=SYSTEM,
                               callback_handler=None)
    parsed = run_json(make_agent, _prompt(batch, resume_text), SPEC, model_id_of(model), guard)
    if not parsed:
        return []
    return [r for r in parsed["results"] if isinstance(r, dict)]


async def score_all(postings: list[Posting], resume_text: str, guard=None,
                    on_batch=None, concurrency: int = 4) -> tuple[list[dict], bool]:
    """Parallel fan-out. A failed batch yields nothing without affecting the others."""
    groups = batches(postings)
    sem = asyncio.Semaphore(concurrency)
    completed = 0

    async def run_one(group: list[Posting]) -> list[dict]:
        nonlocal completed
        async with sem:
            proposals = await asyncio.to_thread(score_batch, group, resume_text, guard)
        completed += 1
        if on_batch:
            on_batch(completed, len(groups), len(proposals))
        return proposals

    gathered = await asyncio.gather(*(run_one(g) for g in groups), return_exceptions=True)
    results, halted = [], False
    for item in gathered:
        if isinstance(item, BudgetExceeded):
            halted = True
        elif not isinstance(item, BaseException):
            results.extend(item)
    return results, halted
