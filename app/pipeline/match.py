"""Scores every profile against a lead and picks the best one (spec §7.2).

No chat LLM here — only embeddings (numbers) and simple arithmetic:
    total = 0.50·semantic + 0.35·skill_overlap + 0.15·exp_fit   (weights from config)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.config import Settings
from app.models.lead import ParsedLead
from app.models.match import MatchScore
from app.pipeline.cv_text import bullets_text
from app.pipeline.skills import normalize_skills, text_mentions


def lead_embedding_text(lead: ParsedLead) -> str:
    """Text we embed for a lead: title + required skills + tools + responsibilities."""
    return "\n".join([
        lead.title,
        "Skills: " + ", ".join(lead.required_skills),
        "Tools: " + ", ".join(lead.tools),
        "Responsibilities: " + "; ".join(lead.responsibilities),
    ])


def profile_embedding_text(cv: dict) -> str:
    """Text we embed for a profile: headline + summary + skills + all roles."""
    roles = "; ".join(f"{job['role']} at {job['company']}" for job in cv.get("experience", []))
    return "\n".join([
        cv.get("headline", ""),
        cv.get("summary", ""),
        "Skills: " + ", ".join(cv.get("skills", [])),
        "Roles: " + roles,
    ])


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity: 1.0 = same direction (very similar), 0 = unrelated."""
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(va @ vb / denom) if denom else 0.0


def skill_overlap(lead: ParsedLead, cv: dict) -> tuple[float, list[str], list[str]]:
    """Share of the lead's required skills the profile has (skills list OR bullets).

    Falls back to `tools` if the lead lists no required skills; 0.5 if neither exists.
    """
    wanted = normalize_skills(lead.required_skills) or normalize_skills(lead.tools)
    if not wanted:
        return 0.5, [], []
    profile_skills = set(normalize_skills(cv.get("skills", [])))
    bullets = bullets_text(cv)
    matched = [s for s in wanted if s in profile_skills or text_mentions(bullets, s)]
    missing = [s for s in wanted if s not in matched]
    return len(matched) / len(wanted), matched, missing


def exp_fit(required_years: float | None, have_years: float) -> float:
    """1.0 if the profile has enough years (or none required), less the further below."""
    if not required_years or have_years >= required_years:
        return 1.0
    return max(0.0, 1 - (required_years - have_years) / required_years)


def score_profile(profile_id: int, profile_cv: dict, profile_embedding: list[float],
                  lead: ParsedLead, lead_embedding: list[float], settings: Settings) -> MatchScore:
    semantic = cosine(lead_embedding, profile_embedding)
    overlap, matched, missing = skill_overlap(lead, profile_cv)
    fit = exp_fit(lead.years_experience_required, float(profile_cv.get("total_years_experience", 0)))
    total = (settings.match_weight_semantic * semantic
             + settings.match_weight_skills * overlap
             + settings.match_weight_experience * fit)
    return MatchScore(profile_id=profile_id, semantic=round(semantic, 4), skill_overlap=round(overlap, 4),
                      exp_fit=round(fit, 4), total=round(total, 4),
                      matched_skills=matched, missing_skills=missing)


@dataclass
class MatchDecision:
    chosen_profile_id: int | None
    reason: str


def decide(scores: list[MatchScore], settings: Settings) -> MatchDecision:
    """Pick the winner, or ask for human review if the result is weak or too close."""
    if not scores:
        return MatchDecision(None, "No active profiles to match against.")
    ranked = sorted(scores, key=lambda s: s.total, reverse=True)
    best = ranked[0]
    if best.total < settings.match_min_total:
        return MatchDecision(None, f"Best score {best.total:.2f} is below {settings.match_min_total:.2f}.")
    if len(ranked) > 1 and best.total - ranked[1].total < settings.match_min_margin:
        return MatchDecision(None, f"Top two scores are too close ({best.total:.2f} vs {ranked[1].total:.2f}).")
    return MatchDecision(best.profile_id, f"Best score {best.total:.2f}.")
