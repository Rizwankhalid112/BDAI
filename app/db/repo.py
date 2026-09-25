"""All SQL queries live here. Other modules call these functions instead of writing SQL.

Every function opens its own short connection; `with get_connection() as conn`
commits when the block finishes without an error.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import numpy as np
from pgvector import Vector
from psycopg.types.json import Jsonb

from app.db.connection import get_connection

Row = dict[str, Any]


def upsert_profile(slug: str, display_name: str, cv: dict) -> int:
    """Create a profile, or replace it if the slug exists. Clears the old embedding."""
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO profiles (slug, display_name, cv)
               VALUES (%s, %s, %s)
               ON CONFLICT (slug) DO UPDATE
                 SET display_name = EXCLUDED.display_name, cv = EXCLUDED.cv,
                     embedding = NULL, is_active = TRUE, updated_at = now()
               RETURNING id""",
            (slug, display_name, Jsonb(cv)),
        ).fetchone()
    return row["id"]


def list_profiles(active_only: bool = False) -> list[Row]:
    sql = "SELECT id, slug, display_name, cv, is_active, embedding IS NOT NULL AS has_embedding, updated_at FROM profiles"
    if active_only:
        sql += " WHERE is_active"
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY id").fetchall()


def get_profile(profile_id: int) -> Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM profiles WHERE id = %s", (profile_id,)).fetchone()


def get_profiles_with_embeddings() -> list[Row]:
    """Active profiles, with the embedding converted to a plain Python list (or None)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, slug, display_name, cv, embedding FROM profiles WHERE is_active ORDER BY id").fetchall()
    for row in rows:
        emb = row["embedding"]
        if emb is not None:
            row["embedding"] = emb.to_list() if isinstance(emb, Vector) else np.asarray(emb).tolist()
    return rows


def set_profile_embedding(profile_id: int, embedding: list[float]) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE profiles SET embedding = %s WHERE id = %s", (Vector(embedding), profile_id))


def set_profile_active(profile_id: int, active: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE profiles SET is_active = %s, updated_at = now() WHERE id = %s", (active, profile_id))


def content_hash(raw_text: str) -> str:
    """sha256 of the text after lowercasing and collapsing whitespace (for dedupe)."""
    normalized = re.sub(r"\s+", " ", raw_text.strip().lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def create_lead(raw_text: str, source: str = "manual", company: str | None = None,
                source_url: str | None = None, changed_by: str = "system") -> tuple[int, bool]:
    """Insert a lead. Returns (lead_id, created). created=False means it was a duplicate."""
    digest = content_hash(raw_text)
    with get_connection() as conn:
        existing = conn.execute("SELECT id FROM leads WHERE content_hash = %s", (digest,)).fetchone()
        if existing:
            return existing["id"], False
        row = conn.execute(
            """INSERT INTO leads (source, source_url, company, raw_text, content_hash)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (source, source_url or None, company or None, raw_text.strip(), digest),
        ).fetchone()
        conn.execute(
            "INSERT INTO lead_status_history (lead_id, from_status, to_status, changed_by, note) "
            "VALUES (%s, NULL, 'new', %s, 'lead created')",
            (row["id"], changed_by),
        )
    return row["id"], True


def get_lead(lead_id: int) -> Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM leads WHERE id = %s", (lead_id,)).fetchone()


def list_leads(status: str | None = None) -> list[Row]:
    sql = """SELECT l.id, l.title, l.company, l.source, l.status, l.created_at,
                    p.display_name AS assigned_profile
             FROM leads l LEFT JOIN profiles p ON p.id = l.assigned_profile_id"""
    params: tuple = ()
    if status:
        sql += " WHERE l.status = %s"
        params = (status,)
    with get_connection() as conn:
        return conn.execute(sql + " ORDER BY l.id DESC", params).fetchall()


def set_lead_status(lead_id: int, to_status: str, changed_by: str = "system", note: str | None = None) -> None:
    """THE only way to change leads.status. Also writes lead_status_history."""
    with get_connection() as conn:
        row = conn.execute("SELECT status FROM leads WHERE id = %s FOR UPDATE", (lead_id,)).fetchone()
        if row is None:
            raise ValueError(f"Lead {lead_id} not found")
        if row["status"] == to_status and note is None:
            return
        conn.execute("UPDATE leads SET status = %s WHERE id = %s", (to_status, lead_id))
        conn.execute(
            "INSERT INTO lead_status_history (lead_id, from_status, to_status, changed_by, note) "
            "VALUES (%s, %s, %s, %s, %s)",
            (lead_id, row["status"], to_status, changed_by, note),
        )


def save_parsed_lead(lead_id: int, parsed: dict) -> None:
    """Store parsed JSON; fill title/company from it if they were empty."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE leads SET parsed = %s,
                   title = COALESCE(title, %s),
                   company = COALESCE(company, %s)
               WHERE id = %s""",
            (Jsonb(parsed), parsed.get("title"), parsed.get("company"), lead_id),
        )


def set_lead_embedding(lead_id: int, embedding: list[float]) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE leads SET embedding = %s WHERE id = %s", (Vector(embedding), lead_id))


def assign_profile(lead_id: int, profile_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE leads SET assigned_profile_id = %s WHERE id = %s", (profile_id, lead_id))
        conn.execute("UPDATE matches SET is_chosen = (profile_id = %s) WHERE lead_id = %s", (profile_id, lead_id))


def get_status_history(lead_id: int) -> list[Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT from_status, to_status, changed_by, note, changed_at FROM lead_status_history "
            "WHERE lead_id = %s ORDER BY id", (lead_id,)).fetchall()


def replace_matches(lead_id: int, scores: list[dict], chosen_profile_id: int | None) -> None:
    """Delete this lead's old scores and store the new ones."""
    with get_connection() as conn:
        conn.execute("DELETE FROM matches WHERE lead_id = %s", (lead_id,))
        for s in scores:
            conn.execute(
                """INSERT INTO matches (lead_id, profile_id, semantic, skill_overlap, exp_fit, total,
                                        matched_skills, missing_skills, is_chosen)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (lead_id, s["profile_id"], s["semantic"], s["skill_overlap"], s["exp_fit"], s["total"],
                 Jsonb(s["matched_skills"]), Jsonb(s["missing_skills"]), s["profile_id"] == chosen_profile_id),
            )


def get_matches(lead_id: int) -> list[Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT m.*, p.display_name FROM matches m JOIN profiles p ON p.id = m.profile_id
               WHERE m.lead_id = %s ORDER BY m.total DESC""", (lead_id,)).fetchall()


def create_generation(lead_id: int, profile_id: int, llm_model: str, prompt_version: str,
                      tailored_cv: dict, gaps: list[str], flags: list[dict],
                      ats_before: float, ats_after: float) -> int:
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO generations (lead_id, profile_id, llm_model, prompt_version, tailored_cv,
                                        gaps, guardrail_flags, ats_before, ats_after)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
            (lead_id, profile_id, llm_model, prompt_version, Jsonb(tailored_cv),
             Jsonb(gaps), Jsonb(flags), ats_before, ats_after),
        ).fetchone()
    return row["id"]


def add_cover_letter(generation_id: int, text: str, prompt_version: str, flags: list[dict]) -> None:
    """Store the letter, append its prompt version and any flags to the generation."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE generations
               SET cover_letter = %s,
                   prompt_version = prompt_version || '+' || %s,
                   guardrail_flags = COALESCE(guardrail_flags, '[]'::jsonb) || %s
               WHERE id = %s""",
            (text, prompt_version, Jsonb(flags), generation_id),
        )


def get_generation(generation_id: int) -> Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM generations WHERE id = %s", (generation_id,)).fetchone()


def get_latest_generation(lead_id: int) -> Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM generations WHERE lead_id = %s ORDER BY id DESC LIMIT 1", (lead_id,)).fetchone()


def review_generation(generation_id: int, status: str, reviewer: str,
                      final_cover_letter: str | None, final_cv: dict | None) -> None:
    """Mark a generation approved or rejected and store the human-edited versions."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE generations
               SET status = %s, reviewer = %s, final_cover_letter = %s, final_cv = %s,
                   approved_at = CASE WHEN %s = 'approved' THEN now() ELSE NULL END
               WHERE id = %s""",
            (status, reviewer, final_cover_letter, Jsonb(final_cv) if final_cv else None, status, generation_id),
        )


def enqueue_job(job_type: str, payload: dict) -> int:
    with get_connection() as conn:
        row = conn.execute("INSERT INTO jobs (job_type, payload) VALUES (%s, %s) RETURNING id",
                           (job_type, Jsonb(payload))).fetchone()
    return row["id"]


def claim_next_job() -> Row | None:
    """Take the oldest queued job and mark it running. SKIP LOCKED makes this safe
    even if two workers ever run at the same time."""
    with get_connection() as conn:
        return conn.execute(
            """UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1, error = NULL
               WHERE id = (SELECT id FROM jobs WHERE status = 'queued'
                           ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
               RETURNING *""").fetchone()


def finish_job(job_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status = 'done', finished_at = now() WHERE id = %s", (job_id,))


def fail_job(job_id: int, error: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status = 'failed', error = %s, finished_at = now() WHERE id = %s",
                     (error[:4000], job_id))


def requeue_job(job_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE jobs SET status = 'queued', error = NULL, started_at = NULL, finished_at = NULL "
                     "WHERE id = %s", (job_id,))


def requeue_stuck_jobs() -> int:
    """Jobs left 'running' by a worker that crashed go back to the queue."""
    with get_connection() as conn:
        return conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running'").rowcount


def list_jobs(limit: int = 200) -> list[Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT %s", (limit,)).fetchall()


def pending_jobs_for_lead(lead_id: int) -> list[Row]:
    with get_connection() as conn:
        return conn.execute(
            """SELECT id, job_type, status, created_at, started_at FROM jobs
               WHERE status IN ('queued', 'running') AND (payload->>'lead_id')::int = %s
               ORDER BY id""", (lead_id,)).fetchall()


def queue_summary() -> dict[str, int]:
    """Counts per job status, plus how long the oldest queued job has waited (seconds)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
        oldest = conn.execute(
            "SELECT EXTRACT(EPOCH FROM now() - min(created_at))::int AS age FROM jobs WHERE status = 'queued'"
        ).fetchone()
    summary = {r["status"]: r["n"] for r in rows}
    summary["oldest_queued_seconds"] = oldest["age"] or 0
    return summary
