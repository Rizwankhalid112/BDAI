"""Opens connections to PostgreSQL.

Usage:
    from app.db.connection import get_connection

    with get_connection() as conn:
        rows = conn.execute("SELECT 1").fetchall()

The `with` block commits on success and rolls back if an error happens.
"""

from __future__ import annotations

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from app.config import get_settings


def get_connection() -> psycopg.Connection:
    """Return a new connection. Rows come back as dicts, e.g. row["title"]."""
    conn = psycopg.connect(get_settings().database_url, row_factory=dict_row)
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        pass
    return conn
