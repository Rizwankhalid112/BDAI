"""Background worker: takes jobs from the `jobs` table and runs them, one at a time.

Pipeline (each step queues the next one when it succeeds):
    parse -> match -> tailor -> cover_letter
If matching is unsure, the lead stops at `needs_match_review` until a human
picks a profile in the UI (which queues `tailor`).

Run from the project root (keep it running in its own terminal):
    python -m app.worker.worker
"""

from __future__ import annotations

import logging
import signal
import time
import traceback
from collections.abc import Callable

from app.config import get_settings
from app.db import repo
from app.llm.client import embed
from app.models.lead import ParsedLead
from app.models.profile import ProfileCV
from app.pipeline.cover_letter import write_cover_letter
from app.pipeline.match import decide, lead_embedding_text, profile_embedding_text, score_profile
from app.pipeline.parse import parse_lead
from app.pipeline.tailor import tailor_cv

logger = logging.getLogger("worker")
POLL_SECONDS = 2


def handle_embed_profile(payload: dict) -> None:
    profile = repo.get_profile(payload["profile_id"])
    if profile is None:
        raise ValueError(f"Profile {payload['profile_id']} not found")
    repo.set_profile_embedding(profile["id"], embed(profile_embedding_text(profile["cv"])))
    logger.info("profile %s embedded", profile["id"])


def handle_parse(payload: dict) -> None:
    lead_id = payload["lead_id"]
    lead = repo.get_lead(lead_id)
    parsed = parse_lead(lead["raw_text"])
    repo.save_parsed_lead(lead_id, parsed.model_dump())
    repo.set_lead_status(lead_id, "parsed")
    repo.enqueue_job("match", {"lead_id": lead_id})


def handle_match(payload: dict) -> None:
    settings = get_settings()
    lead_id = payload["lead_id"]
    lead = repo.get_lead(lead_id)
    parsed = ParsedLead.model_validate(lead["parsed"])

    lead_vec = embed(lead_embedding_text(parsed))
    repo.set_lead_embedding(lead_id, lead_vec)

    profiles = repo.get_profiles_with_embeddings()
    if not profiles:
        raise ValueError("There are no active profiles. Add a profile first, then retry this job.")

    scores = []
    for p in profiles:
        if p["embedding"] is None:
            p["embedding"] = embed(profile_embedding_text(p["cv"]))
            repo.set_profile_embedding(p["id"], p["embedding"])
        scores.append(score_profile(p["id"], p["cv"], p["embedding"], parsed, lead_vec, settings))

    decision = decide(scores, settings)
    repo.replace_matches(lead_id, [s.model_dump() for s in scores], decision.chosen_profile_id)

    if decision.chosen_profile_id is None:
        repo.set_lead_status(lead_id, "needs_match_review", note=decision.reason)
        return
    repo.assign_profile(lead_id, decision.chosen_profile_id)
    repo.set_lead_status(lead_id, "matched", note=decision.reason)
    repo.enqueue_job("tailor", {"lead_id": lead_id})


def handle_tailor(payload: dict) -> None:
    settings = get_settings()
    lead_id = payload["lead_id"]
    lead = repo.get_lead(lead_id)
    if lead["assigned_profile_id"] is None:
        raise ValueError("Lead has no assigned profile. Choose one in the UI first.")
    profile = repo.get_profile(lead["assigned_profile_id"])
    profile_cv = ProfileCV.model_validate(profile["cv"]).model_dump()
    parsed = ParsedLead.model_validate(lead["parsed"])

    out = tailor_cv(profile_cv, parsed)
    g = out.guardrails
    generation_id = repo.create_generation(
        lead_id, profile["id"], settings.llm_model, out.prompt_version, g.cv, g.gaps,
        [f.to_dict() for f in g.flags], out.ats_before, out.ats_after)

    errors = sum(1 for f in g.flags if f.severity == "error")
    repo.set_lead_status(lead_id, "tailored",
                         note=f"generation {generation_id}; ATS {out.ats_before:.0f} -> {out.ats_after:.0f}; "
                              f"{errors} guardrail error(s)")
    repo.enqueue_job("cover_letter", {"lead_id": lead_id, "generation_id": generation_id})


def handle_cover_letter(payload: dict) -> None:
    lead_id = payload["lead_id"]
    generation = repo.get_generation(payload["generation_id"])
    lead = repo.get_lead(lead_id)
    profile_cv = repo.get_profile(generation["profile_id"])["cv"]
    parsed = ParsedLead.model_validate(lead["parsed"])

    out = write_cover_letter(generation["tailored_cv"], profile_cv, parsed)
    repo.add_cover_letter(generation["id"], out.text, out.prompt_version, [f.to_dict() for f in out.flags])
    repo.set_lead_status(lead_id, "in_review", note=f"generation {generation['id']} ready for review")


HANDLERS: dict[str, Callable[[dict], None]] = {
    "embed_profile": handle_embed_profile,
    "parse": handle_parse,
    "match": handle_match,
    "tailor": handle_tailor,
    "cover_letter": handle_cover_letter,
}


def run_one_job() -> bool:
    """Run the next queued job. Returns False if the queue was empty."""
    job = repo.claim_next_job()
    if job is None:
        return False
    started = time.monotonic()
    logger.info("job %s (%s) started", job["id"], job["job_type"])
    try:
        HANDLERS[job["job_type"]](job["payload"])
        repo.finish_job(job["id"])
        logger.info("job %s done in %.1fs", job["id"], time.monotonic() - started)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.error("job %s failed: %s\n%s", job["id"], error, traceback.format_exc())
        repo.fail_job(job["id"], error)
        lead_id = job["payload"].get("lead_id")
        if lead_id is not None:
            repo.set_lead_status(lead_id, "failed", note=f"{job['job_type']} failed: {error}"[:500])
    return True


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = False

    def _stop(*_):
        nonlocal stop
        stop = True
        logger.info("stopping after the current job...")

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    restored = repo.requeue_stuck_jobs()
    if restored:
        logger.info("re-queued %d job(s) left running by a previous worker", restored)
    logger.info("worker started (model %s); waiting for jobs", get_settings().llm_model)
    while not stop:
        if not run_one_job():
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
