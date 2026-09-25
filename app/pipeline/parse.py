"""Step 1: turn a raw job post into structured JSON (spec §7.1)."""

from __future__ import annotations

import re

from app.config import get_settings
from app.llm.client import chat_json
from app.llm.prompt_loader import load_prompt
from app.models.lead import ParsedLead
from app.pipeline.skills import normalize_skills

YEARS_PATTERN = re.compile(r"\b(\d{1,2})\s*(?:\+|-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years|yrs)\b", re.IGNORECASE)


def years_from_text(text: str) -> float | None:
    """Minimum years of experience written in the post, or None if no such phrase exists.

    Used only when the model leaves years_experience_required empty — in testing,
    qwen3 returned null even for posts that clearly say "5+ years".
    """
    match = YEARS_PATTERN.search(text)
    return float(match.group(1)) if match else None


def parse_lead(raw_text: str) -> ParsedLead:
    """Ask the LLM to extract facts, then normalize the skill names."""
    prompt = load_prompt("parse")
    parsed = chat_json(
        [{"role": "system", "content": prompt.text},
         {"role": "user", "content": f"Job post:\n<<<\n{raw_text.strip()}\n>>>"}],
        ParsedLead,
        temperature=get_settings().temperature_parse,
    )
    if parsed.years_experience_required is None:
        parsed = parsed.model_copy(update={"years_experience_required": years_from_text(raw_text)})
    return normalize_parsed(parsed)


def normalize_parsed(parsed: ParsedLead) -> ParsedLead:
    """Normalize skills and remove repeats across the three lists (required wins)."""
    required = normalize_skills(parsed.required_skills)
    nice = [s for s in normalize_skills(parsed.nice_to_have_skills) if s not in required]
    tools = [s for s in normalize_skills(parsed.tools) if s not in required and s not in nice]
    return parsed.model_copy(update={"required_skills": required, "nice_to_have_skills": nice, "tools": tools})
