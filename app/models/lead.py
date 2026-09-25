"""Pydantic model for a parsed job post (spec §7.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ParsedLead(BaseModel):
    title: str
    company: str | None = None
    seniority: Literal["junior", "mid", "senior", "lead"] | None = None
    years_experience_required: float | None = None
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    domain: str | None = None
    location: str | None = None
    remote: bool | None = None
    responsibilities: list[str] = Field(default_factory=list)
