"""Truthfulness checks that run AFTER the LLM tailors a CV (spec §7.4).

The prompt asks the model not to invent anything, but we don't trust that
alone. This code:
  1. Forces company / role / dates / education / certifications back to the
     profile's exact values (and flags any difference).
  2. Keeps the skills list to exactly the profile's skills (removes extras).
  3. Checks every rewritten bullet against the SAME role's original bullets:
     it must be a rephrase of one of them, and it must not mention a
     technology that role never mentioned (no "moving" skills between jobs,
     no copying the job post's responsibilities into the CV).
  4. Finds "invented claims": job keywords that appear in the tailored text
     but nowhere in the original profile.
  5. Lists "gaps": job keywords the person genuinely doesn't have.

Bullet problems (3) are first reported so the caller can retry the LLM once.
With fix_bullets=True, a bad bullet is replaced by the original bullet and
flagged as "fixed", so no invented text can reach the reviewer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.generation import TailoredCV
from app.models.lead import ParsedLead
from app.pipeline.ats import lead_keywords
from app.pipeline.cv_text import cv_to_text
from app.pipeline.skills import normalize_skill, normalize_skills, text_mentions

MIN_BULLET_OVERLAP = 0.35

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at", "by", "with", "from",
    "into", "using", "via", "that", "this", "which", "as", "is", "was", "were", "be", "been",
    "our", "their", "its", "per", "over", "across", "through", "while", "than", "then", "also",
}


@dataclass
class Flag:
    severity: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return {"severity": self.severity, "code": self.code, "message": self.message}


@dataclass
class GuardrailResult:
    cv: dict
    flags: list[Flag] = field(default_factory=list)
    invented: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def find_invented(text: str, profile: dict, lead: ParsedLead) -> list[str]:
    """Job keywords that `text` mentions but the original profile never does."""
    profile_text = cv_to_text(profile)
    return [kw for kw in lead_keywords(lead)
            if text_mentions(text, kw) and not text_mentions(profile_text, kw)]


def find_gaps(profile: dict, lead: ParsedLead) -> list[str]:
    """Job keywords the profile does not contain anywhere."""
    profile_text = cv_to_text(profile)
    return [kw for kw in lead_keywords(lead) if not text_mentions(profile_text, kw)]


def _content_words(text: str) -> set[str]:
    """Lowercase words that carry meaning (no stopwords, no 1–2 letter words)."""
    words = re.findall(r"[a-z0-9][a-z0-9+#./-]*", text.lower())
    return {w.strip("./-") for w in words if len(w) > 2 and w not in STOPWORDS}


def bullet_overlap(tailored: str, original: str) -> float:
    """Share of the tailored bullet's content words that also appear in the original (0–1)."""
    t, o = _content_words(tailored), _content_words(original)
    return len(t & o) / len(t) if t else 1.0


def _term_vocabulary(profile: dict, lead: ParsedLead) -> list[str]:
    """Technology terms we watch for: the profile's skills plus the job's keywords."""
    return normalize_skills(list(profile.get("skills", [])) + list(lead_keywords(lead)))


def _role_text(job: dict) -> str:
    return "\n".join([job.get("role", ""), *job.get("bullets", [])])


def check_bullet(bullet: str, original_role: dict, vocab: list[str]) -> tuple[int | None, float, list[str]]:
    """Compare one tailored bullet with a role's original bullets.

    Returns (index of the best-matching original bullet, its overlap, list of reasons
    the bullet is NOT acceptable). An empty reasons list means the bullet is fine.
    """
    originals = original_role.get("bullets", [])
    best_j, best = None, 0.0
    for j, orig in enumerate(originals):
        score = bullet_overlap(bullet, orig)
        if score > best:
            best_j, best = j, score
    reasons: list[str] = []
    if best < MIN_BULLET_OVERLAP:
        reasons.append("does not match any original bullet of this role")
    role_text = _role_text(original_role)
    moved = [t for t in vocab if text_mentions(bullet, t) and not text_mentions(role_text, t)]
    if moved:
        reasons.append("mentions " + ", ".join(f"'{t}'" for t in moved) + " which this role's original text does not")
    return best_j, best, reasons


def _fix_role_bullets(i: int, tailored_bullets: list[str], orig: dict, vocab: list[str],
                      flags: list[Flag], problems: list[str], fix_bullets: bool) -> list[str]:
    """Return the bullets to keep for role i (see module docstring)."""
    originals = orig["bullets"]
    checked = [(b, *check_bullet(b, orig, vocab)) for b in tailored_bullets]
    used = {best_j for _, best_j, _, reasons in checked if not reasons and best_j is not None}

    kept: list[str] = []
    for b, best_j, best, reasons in checked:
        if not reasons:
            kept.append(b)
            continue
        desc = f"Role {i + 1} ({orig['role']}), bullet \"{b[:70]}…\": " + "; ".join(reasons)
        problems.append(desc)
        if not fix_bullets:
            flags.append(Flag("error", "bullet_not_from_profile", desc + ". Check it before approving."))
            kept.append(b)
            continue
        free = [j for j in range(len(originals)) if j not in used]
        j = best_j if (best_j in free and best >= MIN_BULLET_OVERLAP) else (free[0] if free else None)
        if j is None:
            flags.append(Flag("fixed", "bullet_dropped", desc + ". It was removed."))
        else:
            used.add(j)
            kept.append(originals[j])
            flags.append(Flag("fixed", "bullet_restored", desc + ". It was replaced with the original bullet."))
    return kept or list(originals)


def _fix_experience(tailored: TailoredCV, profile: dict, lead: ParsedLead, flags: list[Flag],
                    problems: list[str], fix_bullets: bool) -> list[dict]:
    """Same entries, same order, facts copied from the profile; bullets checked per role."""
    original = profile.get("experience", [])
    vocab = _term_vocabulary(profile, lead)
    if len(tailored.experience) != len(original):
        flags.append(Flag("fixed", "experience_count",
                          f"Model returned {len(tailored.experience)} roles, profile has {len(original)}. "
                          "Missing roles were restored from the profile."))
    fixed: list[dict] = []
    for i, orig in enumerate(original):
        new = tailored.experience[i] if i < len(tailored.experience) else None
        if new is None:
            fixed.append(dict(orig))
            continue
        for key in ("company", "role", "start", "end"):
            if getattr(new, key).strip() != str(orig[key]).strip():
                flags.append(Flag("fixed", f"changed_{key}",
                                  f"Role {i + 1}: model changed {key} — restored the profile value."))
        bullets = [b.strip() for b in new.bullets if b.strip()]
        if len(bullets) > len(orig["bullets"]):
            flags.append(Flag("fixed", "too_many_bullets",
                              f"Role {i + 1}: model returned {len(bullets)} bullets, profile has "
                              f"{len(orig['bullets'])}. Extra bullets were dropped."))
            bullets = bullets[: len(orig["bullets"])]
        bullets = _fix_role_bullets(i, bullets, orig, vocab, flags, problems, fix_bullets)
        fixed.append({"company": orig["company"], "role": orig["role"],
                      "start": orig["start"], "end": orig["end"], "bullets": bullets})
    return fixed


def _fix_skills(tailored: TailoredCV, profile: dict, flags: list[Flag]) -> list[str]:
    """Keep the model's order, but only skills the profile really has; re-add any it dropped."""
    profile_skills = {normalize_skill(s): s for s in profile.get("skills", [])}
    kept: dict[str, str] = {}
    for skill in tailored.skills:
        key = normalize_skill(skill)
        if key in profile_skills:
            kept.setdefault(key, skill.strip())
        else:
            flags.append(Flag("fixed", "extra_skill", f"Removed skill not in profile: '{skill}'."))
    dropped = [original for key, original in profile_skills.items() if key not in kept]
    if dropped:
        flags.append(Flag("info", "restored_skills", f"Re-added skills the model left out: {', '.join(dropped)}."))
    return list(kept.values()) + dropped


def apply_guardrails(tailored: TailoredCV, profile: dict, lead: ParsedLead,
                     fix_bullets: bool = False) -> GuardrailResult:
    """Return a corrected tailored CV plus flags, invented claims, gaps and bullet problems.

    fix_bullets=False: bad bullets are kept but flagged as errors and listed in
    `problems` (so the caller can retry the LLM). fix_bullets=True: bad bullets
    are replaced by the original bullet and flagged as "fixed".
    """
    flags: list[Flag] = []
    problems: list[str] = []
    cv = {
        "name": profile["name"],
        "headline": profile["headline"],
        "total_years_experience": profile["total_years_experience"],
        "contact": profile.get("contact", {}),
        "education": profile.get("education", []),
        "certifications": profile.get("certifications", []),
        "summary": tailored.summary.strip(),
        "skills": _fix_skills(tailored, profile, flags),
        "experience": _fix_experience(tailored, profile, lead, flags, problems, fix_bullets),
    }
    tailored_text = "\n".join([cv["summary"], ", ".join(cv["skills"]),
                               *(b for job in cv["experience"] for b in job["bullets"])])
    invented = find_invented(tailored_text, profile, lead)
    for kw in invented:
        flags.append(Flag("error", "invented_claim",
                          f"'{kw}' appears in the tailored CV but not in the original profile. "
                          "Remove it before approving."))
    return GuardrailResult(cv=cv, flags=flags, invented=invented, gaps=find_gaps(profile, lead), problems=problems)
