"""Turns a CV (dict) into plain text, so we can search it for keywords."""

from __future__ import annotations


def cv_to_text(cv: dict) -> str:
    """All searchable text of a CV. Contact details are deliberately left out."""
    parts: list[str] = [cv.get("headline", ""), cv.get("summary", "")]
    parts.append(", ".join(cv.get("skills", [])))
    for job in cv.get("experience", []):
        parts.append(f"{job.get('role', '')} at {job.get('company', '')}")
        parts.extend(job.get("bullets", []))
    for edu in cv.get("education", []):
        parts.append(f"{edu.get('degree', '')} {edu.get('institution', '')}")
    parts.extend(cv.get("certifications", []))
    return "\n".join(p for p in parts if p)


def bullets_text(cv: dict) -> str:
    """Only the experience bullets (used to find skills mentioned in bullets)."""
    return "\n".join(b for job in cv.get("experience", []) for b in job.get("bullets", []))
