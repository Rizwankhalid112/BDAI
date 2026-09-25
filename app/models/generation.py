"""Pydantic models for what the tailoring step returns (spec §7.3)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TailoredExperience(BaseModel):
    company: str
    role: str
    start: str
    end: str
    bullets: list[str] = Field(default_factory=list)


class TailoredCV(BaseModel):
    """What the LLM returns. Education, certifications, name and contact are
    NOT part of this — code copies them from the profile unchanged."""

    summary: str
    skills: list[str]
    experience: list[TailoredExperience]
