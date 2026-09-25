"""ATS keyword score (spec §7.5). Pure code, no AI.

An ATS (Applicant Tracking System) mostly checks whether the CV contains the
job's keywords. We imitate that:
  - required skills are worth 2 points, nice-to-have skills and tools 1 point
  - a keyword counts if the CV text mentions it (any synonym, whole words)
  - score = points found / points possible × 100
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.lead import ParsedLead
from app.pipeline.cv_text import cv_to_text
from app.pipeline.skills import normalize_skills, text_mentions


@dataclass
class ATSResult:
    score: float
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def lead_keywords(lead: ParsedLead) -> dict[str, int]:
    """Normalized keyword -> weight. If a keyword appears twice, the higher weight wins."""
    weights: dict[str, int] = {}
    for kw in normalize_skills(lead.nice_to_have_skills + lead.tools):
        weights[kw] = 1
    for kw in normalize_skills(lead.required_skills):
        weights[kw] = 2
    return weights


def ats_score(cv: dict, lead: ParsedLead) -> ATSResult:
    """Score a CV (profile or tailored) against a parsed lead."""
    weights = lead_keywords(lead)
    if not weights:
        return ATSResult(score=0.0)
    text = cv_to_text(cv)
    matched = [kw for kw in weights if text_mentions(text, kw)]
    missing = [kw for kw in weights if kw not in matched]
    score = sum(weights[kw] for kw in matched) / sum(weights.values()) * 100
    return ATSResult(score=round(score, 1), matched=matched, missing=missing)
