"""Per-worker analysis capacity, keyed by ``worker_key``.

A row is configuration only: how many analyses that worker may run at once.
It is never evidence that the worker exists or is running.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

MIN_CAPACITY = 1
MAX_CAPACITY = 16
DEFAULT_CAPACITY = 1

_SCHEMA_LOCK = 8_273_341_905
_SCHEMA = (
    f"""
    CREATE TABLE IF NOT EXISTS analysis_worker_capacity (
        worker_key text PRIMARY KEY,
        max_concurrent_jobs integer NOT NULL
            CHECK (max_concurrent_jobs BETWEEN {MIN_CAPACITY} AND {MAX_CAPACITY})
    )
    """,
)


class WorkerCapacityStore:
    """Read and save one worker's capacity. Other workers keep their own rows."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def ensure_schema(self) -> None:
        """Create the capacity table when it is missing."""
        with self._conn() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            for statement in _SCHEMA:
                conn.execute(statement)

    def clear(self) -> None:
        """Delete every capacity row. Used by tests, not by the public site."""
        with self._conn() as conn:
            conn.execute("TRUNCATE analysis_worker_capacity")

    def capacity(self, worker_key: str, default: int = DEFAULT_CAPACITY) -> int:
        """Saved capacity for ``worker_key``, or ``default`` when none was saved."""
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT max_concurrent_jobs FROM analysis_worker_capacity
                WHERE worker_key = %s
                """,
                (worker_key,),
            ).fetchone()
        if row is None:
            return default
        return int(row["max_concurrent_jobs"])

    def set_capacity(self, worker_key: str, max_concurrent_jobs: int) -> int:
        """Save the capacity of ``worker_key`` only."""
        if not MIN_CAPACITY <= max_concurrent_jobs <= MAX_CAPACITY:
            raise ValueError(f"worker capacity must be from {MIN_CAPACITY} to {MAX_CAPACITY}")
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO analysis_worker_capacity (worker_key, max_concurrent_jobs)
                VALUES (%s, %s)
                ON CONFLICT (worker_key) DO UPDATE
                    SET max_concurrent_jobs = EXCLUDED.max_concurrent_jobs
                """,
                (worker_key, max_concurrent_jobs),
            )
        return max_concurrent_jobs

    def _conn(self) -> psycopg.Connection:
        return psycopg.connect(self.database_url, row_factory=dict_row)
