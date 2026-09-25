"""Step 4: write a short cover letter from the lead + tailored CV (spec §7.6).

Flow:
  1. Tell the LLM which job keywords the person does NOT have (the gaps), so it
     knows not to mention them.
  2. If the letter still claims one of them, retry ONCE with a stricter message.
  3. Code checks (length, clichés, placeholders, invented claims) flag anything
     left for the reviewer. Nothing is silently fixed.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from app.config import get_settings
from app.llm.client import Message, chat_text
from app.llm.prompt_loader import load_prompt
from app.models.lead import ParsedLead
from app.pipeline.guardrails import Flag, find_gaps, find_invented

logger = logging.getLogger(__name__)

MAX_WORDS = 250
CLICHES = ["i am writing to", "i am excited to apply", "passionate", "team player", "go-getter"]


@dataclass
class CoverLetterOutput:
    text: str
    prompt_version: str
    flags: list[Flag] = field(default_factory=list)


def check_cover_letter(text: str, profile: dict, lead: ParsedLead) -> list[Flag]:
    """Code checks on the letter. Problems are flagged for the reviewer, never hidden."""
    flags: list[Flag] = []
    words = len(text.split())
    if words > MAX_WORDS:
        flags.append(Flag("error", "letter_too_long", f"Cover letter has {words} words (limit {MAX_WORDS})."))
    lowered = text.lower()
    for phrase in CLICHES:
        if phrase in lowered:
            flags.append(Flag("error", "letter_cliche", f"Cover letter uses the cliché '{phrase}'."))
    if re.search(r"\[[^\]]+\]", text):
        flags.append(Flag("error", "letter_placeholder", "Cover letter contains a [placeholder]."))
    for kw in find_invented(text, profile, lead):
        flags.append(Flag("error", "letter_invented_claim",
                          f"Cover letter mentions '{kw}', which is not in the profile."))
    return flags


def write_cover_letter(tailored_cv: dict, profile: dict, lead: ParsedLead) -> CoverLetterOutput:
    prompt = load_prompt("cover_letter")
    temperature = get_settings().temperature_write
    job = lead.model_dump(include={"title", "company", "required_skills", "nice_to_have_skills",
                                   "tools", "responsibilities"})
    cv = {key: tailored_cv[key] for key in ("name", "headline", "total_years_experience",
                                            "summary", "skills", "experience")}
    user_message = ("JOB POST (parsed):\n" + json.dumps(job, indent=1)
                    + "\n\nCANDIDATE CV:\n" + json.dumps(cv, indent=1))
    gaps = find_gaps(profile, lead)
    if gaps:
        user_message += ("\n\nThe candidate does NOT have these skills from the job post. "
                         "Never mention them, not even as something to learn: " + ", ".join(gaps))
    messages: list[Message] = [{"role": "system", "content": prompt.text},
                               {"role": "user", "content": user_message}]
    text = chat_text(messages, temperature=temperature)

    invented = find_invented(text, profile, lead)
    if invented:
        logger.warning("cover letter: invented terms found, retrying once with a stricter prompt")
        stricter = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Your letter mentions these skills the candidate does NOT have: "
                                        + ", ".join(invented)
                                        + ". Rewrite the whole letter without any mention of them. "
                                          "Do not replace them with other skills that are not in the CV."},
        ]
        text = chat_text(stricter, temperature=temperature)

    return CoverLetterOutput(text=text, prompt_version=prompt.version,
                             flags=check_cover_letter(text, profile, lead))
