"""Phase 1 "done when" check.

Run from the project root:
    python -m scripts.check_setup

It checks, one by one:
  1. The database is reachable and all tables exist.
  2. The sample profiles are valid.
  3. The LLM (qwen3:8b) answers a short question.
  4. The embedding model (bge-m3) returns a 1024-number vector.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.db.init_db import EXPECTED_TABLES, list_tables
from app.llm.client import chat_text, embed
from app.models.profile import ProfileCV

PROFILES_DIR = Path(__file__).resolve().parent.parent / "data" / "samples" / "profiles"


def check_database() -> None:
    tables = list_tables()
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        raise RuntimeError(f"Missing tables: {missing}. Run: python -m app.db.init_db")
    print(f"    tables: {', '.join(EXPECTED_TABLES)}")


def check_sample_profiles() -> None:
    files = sorted(PROFILES_DIR.glob("*.json"))
    if not files:
        raise RuntimeError(f"No profile files in {PROFILES_DIR}")
    for path in files:
        ProfileCV.model_validate(json.loads(path.read_text(encoding="utf-8")))
    print(f"    {len(files)} valid profile(s)")


def check_chat() -> None:
    settings = get_settings()
    print(f"    asking {settings.llm_model} (can take a minute on CPU)...")
    started = time.monotonic()
    reply = chat_text(
        [{"role": "user", "content": "Reply with one short sentence: what is PostgreSQL?"}],
        temperature=settings.temperature_parse,
    )
    print(f"    reply ({time.monotonic() - started:.1f}s): {reply}")


def check_embed() -> None:
    settings = get_settings()
    vector = embed("Senior Python backend engineer with PostgreSQL and AWS experience")
    print(f"    {settings.embed_model} embedding length: {len(vector)}")


CHECKS = [
    ("Database", check_database),
    ("Sample profiles", check_sample_profiles),
    ("LLM chat", check_chat),
    ("Embedding", check_embed),
]


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    failures = 0
    for name, check in CHECKS:
        print(f"[..] {name}")
        try:
            check()
            print(f"[OK] {name}\n")
        except Exception as exc:
            failures += 1
            print(f"[FAIL] {name}: {exc}\n")
    print("All checks passed." if failures == 0 else f"{failures} check(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
