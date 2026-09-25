"""Actions the UI can trigger. The UI calls these; these call repo.py.

Keeping this logic out of the Streamlit file means it can be tested and later
reused by an API without copying code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.db import repo
from app.models.profile import ProfileCV

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples"


def slugify(text: str) -> str:
    """'Sample Person A' -> 'sample-person-a'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "profile"


def save_profile(cv_data: dict) -> int:
    """Validate a profile CV, store it, and queue its embedding."""
    cv = ProfileCV.model_validate(cv_data)
    profile_id = repo.upsert_profile(slugify(cv.name), cv.name, cv.model_dump())
    repo.enqueue_job("embed_profile", {"profile_id": profile_id})
    return profile_id


def load_sample_profiles() -> list[int]:
    return [save_profile(_read_json(p)) for p in sorted((SAMPLES_DIR / "profiles").glob("*.json"))]


def sample_lead_files() -> list[Path]:
    return sorted((SAMPLES_DIR / "leads").glob("*.txt"))


def add_lead(raw_text: str, company: str | None = None, source_url: str | None = None,
             source: str = "manual", changed_by: str = "system") -> tuple[int, bool]:
    """Store a lead and start the pipeline. Returns (lead_id, created)."""
    if not raw_text.strip():
        raise ValueError("The job post text is empty.")
    lead_id, created = repo.create_lead(raw_text, source=source, company=company,
                                        source_url=source_url, changed_by=changed_by)
    if created:
        repo.enqueue_job("parse", {"lead_id": lead_id})
    return lead_id, created


def choose_profile(lead_id: int, profile_id: int, reviewer: str) -> None:
    """Manual override / human decision: assign a profile and start tailoring."""
    repo.assign_profile(lead_id, profile_id)
    profile = repo.get_profile(profile_id)
    repo.set_lead_status(lead_id, "matched", changed_by=reviewer,
                         note=f"profile chosen manually: {profile['slug']}")
    repo.enqueue_job("tailor", {"lead_id": lead_id})


def regenerate(lead_id: int, reviewer: str) -> None:
    repo.set_lead_status(lead_id, "matched", changed_by=reviewer, note="regenerate requested")
    repo.enqueue_job("tailor", {"lead_id": lead_id})


def review(lead_id: int, generation_id: int, approve: bool, reviewer: str,
           final_cover_letter: str, final_cv: dict | None) -> None:
    status = "approved" if approve else "rejected"
    repo.review_generation(generation_id, status, reviewer, final_cover_letter, final_cv)
    repo.set_lead_status(lead_id, status, changed_by=reviewer, note=f"generation {generation_id} {status}")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
