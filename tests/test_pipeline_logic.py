"""Tests for the code-only pipeline parts: skills, ATS, matching, guardrails. No AI calls."""

import json
from pathlib import Path

import pytest

from app import config
from app.models.generation import TailoredCV
from app.models.lead import ParsedLead
from app.pipeline import match
from app.pipeline.ats import ats_score
from app.pipeline.cover_letter import check_cover_letter
from app.pipeline.guardrails import apply_guardrails
from app.pipeline.skills import normalize_skill, normalize_skills, text_mentions

PROFILE_A = json.loads((Path(__file__).resolve().parent.parent / "data/samples/profiles/sample_person_a.json").read_text())

LEAD = ParsedLead(
    title="Senior Backend Engineer",
    years_experience_required=5,
    required_skills=["python", "postgresql", "aws", "go"],
    nice_to_have_skills=["kubernetes", "kafka"],
    tools=["terraform"],
)


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        monkeypatch.setenv(key, "x")
    config.get_settings.cache_clear()
    yield config.get_settings()
    config.get_settings.cache_clear()


def tailored_from_profile(**changes) -> TailoredCV:
    """A 'perfect' tailored CV (a copy of the profile), with optional changes."""
    data = {"summary": PROFILE_A["summary"], "skills": list(PROFILE_A["skills"]),
            "experience": [dict(job) for job in PROFILE_A["experience"]]}
    data.update(changes)
    return TailoredCV.model_validate(data)


@pytest.mark.parametrize("raw, expected", [
    ("  Postgres ", "postgresql"), ("K8s", "kubernetes"), ("JS", "javascript"),
    ("node", "node.js"), ("React.js", "react"), ("Python", "python"),
])
def test_normalize_skill(raw, expected):
    assert normalize_skill(raw) == expected


def test_normalize_skills_dedupes():
    assert normalize_skills(["Postgres", "PostgreSQL", "postgresql "]) == ["postgresql"]


def test_text_mentions_uses_synonyms_and_word_boundaries():
    assert text_mentions("Ran services on k8s", "kubernetes")
    assert text_mentions("Built with Node.js", "node.js")
    assert text_mentions("CI/CD pipelines", "ci/cd")
    assert not text_mentions("Wrote JavaScript", "java")


@pytest.mark.parametrize("lead_file, expected", [
    ("lead_01_python_backend.txt", 5), ("lead_02_django_developer.txt", 4), ("lead_03_devops_platform.txt", 5),
    ("lead_04_data_api.txt", 3), ("lead_05_react_frontend.txt", 3), ("lead_06_nextjs_engineer.txt", 3),
    ("lead_07_ui_component_library.txt", 2), ("lead_08_javascript_web.txt", 2),
    ("lead_09_fullstack_mixed.txt", 4), ("lead_10_java_sap.txt", 8),
])
def test_years_from_text_on_sample_leads(lead_file, expected):
    from app.pipeline.parse import years_from_text
    text = (Path(__file__).resolve().parent.parent / "data/samples/leads" / lead_file).read_text()
    assert years_from_text(text) == expected


def test_years_from_text_ignores_other_numbers():
    from app.pipeline.parse import years_from_text
    assert years_from_text("Built 40+ Airflow DAGs; cut run time from 3 hours to 45 minutes. WCAG 2.1 AA.") is None
    assert years_from_text("3 to 5 yrs of experience") == 3


def test_ats_weights_required_double():
    result = ats_score(PROFILE_A, LEAD)
    assert result.score == round(8 / 11 * 100, 1)
    assert set(result.missing) == {"go", "kafka"}


def test_skill_overlap_counts_skills_in_bullets():
    cv = {"skills": ["Python"], "experience": [{"bullets": ["Deployed on AWS"]}]}
    lead = ParsedLead(title="x", required_skills=["python", "aws", "go"])
    score, matched, missing = match.skill_overlap(lead, cv)
    assert score == pytest.approx(2 / 3)
    assert matched == ["python", "aws"] and missing == ["go"]


def test_skill_overlap_falls_back_to_half():
    assert match.skill_overlap(ParsedLead(title="x"), PROFILE_A)[0] == 0.5


@pytest.mark.parametrize("required, have, expected", [(None, 2, 1.0), (5, 7, 1.0), (8, 4, 0.5), (5, 0, 0.0)])
def test_exp_fit(required, have, expected):
    assert match.exp_fit(required, have) == expected


def test_decide_needs_review_when_close_or_low(settings):
    def s(pid, total):
        return match.MatchScore(profile_id=pid, semantic=0, skill_overlap=0, exp_fit=0, total=total)

    assert match.decide([s(1, 0.80), s(2, 0.60)], settings).chosen_profile_id == 1
    assert match.decide([s(1, 0.80), s(2, 0.78)], settings).chosen_profile_id is None
    assert match.decide([s(1, 0.40), s(2, 0.20)], settings).chosen_profile_id is None
    assert match.decide([s(1, 0.70)], settings).chosen_profile_id == 1


def test_clean_tailoring_has_no_errors():
    result = apply_guardrails(tailored_from_profile(), PROFILE_A, LEAD)
    assert not [f for f in result.flags if f.severity == "error"]
    assert result.invented == []
    assert set(result.gaps) == {"go", "kafka"}


def test_planted_invented_skill_is_flagged_and_removed():
    """The LLM sneaks in 'Kafka' and 'Go' (job keywords the person does NOT have)."""
    experience = [dict(job) for job in PROFILE_A["experience"]]
    experience[0] = {**experience[0], "bullets": ["Built event pipelines with Kafka and Go."]
                     + experience[0]["bullets"][1:]}
    tailored = tailored_from_profile(skills=["Kafka"] + PROFILE_A["skills"], experience=experience)

    result = apply_guardrails(tailored, PROFILE_A, LEAD)

    assert "Kafka" not in result.cv["skills"]
    assert set(result.invented) == {"kafka", "go"}
    codes = [f.code for f in result.flags]
    assert codes.count("invented_claim") == 2
    assert "extra_skill" in codes


def test_changed_facts_are_restored():
    experience = [dict(job) for job in PROFILE_A["experience"]]
    experience[0] = {**experience[0], "company": "Famous Big Tech", "role": "Principal Engineer", "start": "2019-01"}
    result = apply_guardrails(tailored_from_profile(experience=experience), PROFILE_A, LEAD)

    first = result.cv["experience"][0]
    assert (first["company"], first["role"], first["start"]) == ("Example Cloud Co", "Senior Backend Engineer", "2021-03")
    assert {"changed_company", "changed_role", "changed_start"} <= {f.code for f in result.flags}
    assert result.cv["education"] == PROFILE_A["education"]
    assert result.cv["certifications"] == PROFILE_A["certifications"]


def test_extra_bullets_and_missing_roles_are_fixed():
    experience = [dict(job) for job in PROFILE_A["experience"][:2]]
    experience[1] = {**experience[1], "bullets": experience[1]["bullets"] + ["Extra bullet."]}
    result = apply_guardrails(tailored_from_profile(experience=experience), PROFILE_A, LEAD)

    assert len(result.cv["experience"]) == 3
    assert len(result.cv["experience"][1]["bullets"]) == len(PROFILE_A["experience"][1]["bullets"])
    assert {"experience_count", "too_many_bullets"} <= {f.code for f in result.flags}


def test_dropped_skills_are_restored():
    result = apply_guardrails(tailored_from_profile(skills=["PostgreSQL", "Python"]), PROFILE_A, LEAD)
    assert result.cv["skills"][:2] == ["PostgreSQL", "Python"]
    assert len(result.cv["skills"]) == len(PROFILE_A["skills"])


def with_first_bullet(text: str) -> list[dict]:
    """Person A's experience with the first bullet of the first role replaced."""
    experience = [dict(job) for job in PROFILE_A["experience"]]
    experience[0] = {**experience[0], "bullets": [text] + experience[0]["bullets"][1:]}
    return experience


def test_faithful_rephrase_is_not_flagged():
    rephrased = "Improved API performance by cutting PostgreSQL query latency 60% through indexing and query rewrites."
    experience = [dict(job) for job in PROFILE_A["experience"]]
    experience[0] = {**experience[0], "bullets": [rephrased] + experience[0]["bullets"][2:]}
    result = apply_guardrails(tailored_from_profile(experience=experience), PROFILE_A, LEAD)
    assert result.problems == []
    assert not [f for f in result.flags if f.severity == "error"]


def test_job_responsibility_copied_into_cv_is_flagged():
    """The model turned a job responsibility into an 'achievement' that is not in the CV."""
    planted = "Built Python services to ingest and clean CSV and JSON data at scale."
    result = apply_guardrails(tailored_from_profile(experience=with_first_bullet(planted)), PROFILE_A, LEAD)
    assert len(result.problems) == 1 and "does not match any original bullet" in result.problems[0]
    assert [f.code for f in result.flags if f.severity == "error"] == ["bullet_not_from_profile"]
    assert result.cv["experience"][0]["bullets"][0] == planted


def test_technology_moved_between_roles_is_flagged():
    """Celery/Redis are real skills of Person A, but only at Fictional Payments — not at Example Cloud Co."""
    planted = "Designed and built FastAPI microservices on AWS ECS with Celery and Redis background jobs."
    result = apply_guardrails(tailored_from_profile(experience=with_first_bullet(planted)), PROFILE_A, LEAD)
    assert len(result.problems) == 1
    assert "'celery'" in result.problems[0] and "'redis'" in result.problems[0]


def test_fix_bullets_restores_original_and_flags_it():
    planted = "Built Python services to ingest and clean CSV and JSON data at scale."
    result = apply_guardrails(tailored_from_profile(experience=with_first_bullet(planted)), PROFILE_A, LEAD,
                              fix_bullets=True)
    bullets = result.cv["experience"][0]["bullets"]
    assert planted not in bullets
    assert bullets == PROFILE_A["experience"][0]["bullets"]
    assert [f.code for f in result.flags if f.severity != "info"] == ["bullet_restored"]
    assert result.problems


def test_tailor_retries_once_then_fixes_what_persists(monkeypatch, settings):
    from app.pipeline import tailor

    bad = tailored_from_profile(experience=with_first_bullet("Built Python services to ingest CSV and JSON data."))
    replies = [bad, bad]
    calls: list = []
    monkeypatch.setattr(tailor, "chat_json", lambda messages, model, temperature: (calls.append(messages), replies.pop(0))[1])

    out = tailor.tailor_cv(PROFILE_A, LEAD)
    assert len(calls) == 2
    assert "breaks the no-invention rule" in calls[1][-1]["content"]
    assert out.guardrails.cv["experience"][0]["bullets"] == PROFILE_A["experience"][0]["bullets"]
    assert [f.code for f in out.guardrails.flags if f.severity == "fixed"] == ["bullet_restored"]
    assert out.ats_after >= out.ats_before


def test_cover_letter_checks():
    letter = "Dear Hiring Manager,\nI am writing to express my interest. I know Kafka well. [Your Address]"
    codes = {f.code for f in check_cover_letter(letter, PROFILE_A, LEAD)}
    assert codes == {"letter_cliche", "letter_placeholder", "letter_invented_claim"}


def test_cover_letter_tells_model_the_gaps_and_retries_once(monkeypatch, settings):
    """First reply claims Kafka (a gap) -> retry once -> clean reply -> no error flags."""
    from app.pipeline import cover_letter

    calls: list[list[dict]] = []
    replies = ["Dear Hiring Manager,\nI have used Kafka a lot.\nKind regards,\nSample Person A",
               "Dear Hiring Manager,\nI built FastAPI services on AWS.\nKind regards,\nSample Person A"]

    def fake_chat_text(messages, temperature):
        calls.append(messages)
        return replies[len(calls) - 1]

    monkeypatch.setattr(cover_letter, "chat_text", fake_chat_text)
    out = cover_letter.write_cover_letter(PROFILE_A, PROFILE_A, LEAD)

    assert len(calls) == 2
    assert "does NOT have" in calls[0][-1]["content"] and "kafka" in calls[0][-1]["content"]
    assert "kafka" in calls[1][-1]["content"]
    assert out.text == replies[1]
    assert [f.code for f in out.flags] == []


def test_cover_letter_flags_claim_that_survives_retry(monkeypatch, settings):
    from app.pipeline import cover_letter

    monkeypatch.setattr(cover_letter, "chat_text",
                        lambda messages, temperature: "Dear Hiring Manager,\nKafka expert.\nKind regards,\nA")
    out = cover_letter.write_cover_letter(PROFILE_A, PROFILE_A, LEAD)
    assert [f.code for f in out.flags] == ["letter_invented_claim"]
