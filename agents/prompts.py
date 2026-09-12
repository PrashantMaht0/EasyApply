"""Loads agent prompts from prompts/*.md so they can be edited and diffed without touching code."""

import pathlib
from dataclasses import dataclass, field

import yaml

PROMPT_DIR = pathlib.Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class Prompt:
    name: str
    body: str
    version: int = 1
    meta: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.body


_cache: dict[str, Prompt] = {}


def load(name: str) -> Prompt:
    """Reads prompts/<name>.md once and caches it. Front matter is optional."""
    if name in _cache:
        return _cache[name]
    text = (PROMPT_DIR / f"{name}.md").read_text()
    meta: dict = {}
    if text.startswith("---\n"):
        _, front, body = text.split("---\n", 2)
        meta = yaml.safe_load(front) or {}
    else:
        body = text
    prompt = Prompt(name=name, body=body.strip(), version=int(meta.get("version", 1)), meta=meta)
    _cache[name] = prompt
    return prompt


def versions() -> dict[str, int]:
    """Every prompt's version, recorded on the run so a result traces back to its prompts."""
    return {path.stem: load(path.stem).version for path in sorted(PROMPT_DIR.glob("*.md"))}
