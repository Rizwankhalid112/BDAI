"""Creates all tables by running schema.sql. Safe to run repeatedly.

Run from the project root:
    python -m app.db.init_db
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.db.connection import get_connection

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
EXPECTED_TABLES = ["profiles", "leads", "lead_status_history", "matches", "generations", "jobs"]

logger = logging.getLogger(__name__)


def init_db() -> None:
    """Apply schema.sql to the database."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection() as conn:
        conn.execute(sql)
    logger.info("Schema applied.")


def list_tables() -> list[str]:
    """Return the names of the tables in the public schema."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()
    return [row["table_name"] for row in rows]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    init_db()
    print("Tables:", ", ".join(list_tables()))
