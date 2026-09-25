"""Checks that the fake sample data is valid and obviously fake."""

import json
from pathlib import Path

from app.models.profile import ProfileCV

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


def test_profiles_are_valid_and_fake():
    files = sorted((SAMPLES / "profiles").glob("*.json"))
    assert len(files) == 2
    for path in files:
        cv = ProfileCV.model_validate(json.loads(path.read_text(encoding="utf-8")))
        assert cv.name.startswith("Sample Person")
        assert cv.contact.email.endswith("@example.com")


def test_every_eval_case_has_a_lead_file():
    cases = json.loads((Path(__file__).resolve().parent.parent / "eval" / "test_set.json").read_text())["cases"]
    lead_files = {p.name for p in (SAMPLES / "leads").glob("*.txt")}
    assert len(cases) == len(lead_files) == 10
    assert {c["lead_file"] for c in cases} == lead_files
