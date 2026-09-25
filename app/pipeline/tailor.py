"""Step 3: tailor the assigned profile's CV to the lead (spec §7.3 + §7.4).

Flow:
  1. LLM rewrites summary / skills order / bullets.
  2. Guardrails correct facts and look for invented claims.
  3. If invented claims were found, retry ONCE with a stricter message.
  4. Anything still invented is flagged as an error for the reviewer.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.config import get_settings
from app.llm.client import Message, chat_json
from app.llm.prompt_loader import load_prompt
from app.models.generation import TailoredCV
from app.models.lead import ParsedLead
from app.pipeline.ats import ats_score
from app.pipeline.guardrails import GuardrailResult, apply_guardrails

logger = logging.getLogger(__name__)


@dataclass
class TailorOutput:
    guardrails: GuardrailResult
    ats_before: float
    ats_after: float
    prompt_version: str


def _cv_for_llm(profile: dict) -> dict:
    """Only the parts the LLM may rewrite. No name or contact details are sent."""
    return {
        "headline": profile["headline"],
        "total_years_experience": profile["total_years_experience"],
        "summary": profile["summary"],
        "skills": profile["skills"],
        "experience": profile["experience"],
    }


def _job_for_llm(lead: ParsedLead) -> dict:
    return lead.model_dump(include={"title", "company", "seniority", "required_skills",
                                    "nice_to_have_skills", "tools", "domain", "responsibilities"})


def tailor_cv(profile: dict, lead: ParsedLead) -> TailorOutput:
    prompt = load_prompt("tailor")
    temperature = get_settings().temperature_write
    messages: list[Message] = [
        {"role": "system", "content": prompt.text},
        {"role": "user", "content": "JOB POST (parsed):\n" + json.dumps(_job_for_llm(lead), indent=1)
                                    + "\n\nCV TO TAILOR:\n" + json.dumps(_cv_for_llm(profile), indent=1)},
    ]
    tailored = chat_json(messages, TailoredCV, temperature=temperature)
    result = apply_guardrails(tailored, profile, lead)

    if result.invented or result.problems:
        logger.warning("tailor: %d invented term(s), %d bullet problem(s) — retrying once with a stricter prompt",
                       len(result.invented), len(result.problems))
        issues = [f"'{kw}' is not in the CV at all" for kw in result.invented] + result.problems
        stricter = messages + [
            {"role": "assistant", "content": tailored.model_dump_json()},
            {"role": "user", "content": "Your answer breaks the no-invention rule:\n- " + "\n- ".join(issues)
                                        + "\n\nEvery bullet must describe the same achievement as one original "
                                          "bullet of the same role, using only that bullet's technologies and "
                                          "numbers. Remove every term that is not in the CV. Do not replace "
                                          "them with other new terms. Return the corrected JSON."},
        ]
        retry = chat_json(stricter, TailoredCV, temperature=temperature)
        result = apply_guardrails(retry, profile, lead, fix_bullets=True)

    return TailorOutput(
        guardrails=result,
        ats_before=ats_score(profile, lead).score,
        ats_after=ats_score(result.cv, lead).score,
        prompt_version=prompt.version,
    )
