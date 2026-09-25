"""Pydantic model for one profile's match score against a lead (spec §7.2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MatchScore(BaseModel):
    profile_id: int
    semantic: float
    skill_overlap: float
    exp_fit: float
    total: float
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
