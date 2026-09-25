"""Loads prompt files from app/llm/prompts/.

Each file starts with a line like "# version: tailor-v1". The version is saved
with every generation so we always know which prompt produced which output.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str


@lru_cache
def load_prompt(name: str) -> Prompt:
    """Read prompts/<name>.txt and split off its version line."""
    lines = (PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8").splitlines()
    first = lines[0].strip()
    if not first.startswith("# version:"):
        raise ValueError(f"Prompt {name}.txt must start with '# version: ...'")
    return Prompt(version=first.split(":", 1)[1].strip(), text="\n".join(lines[1:]).strip())
