"""Job queue on PostgreSQL: claim with FOR UPDATE SKIP LOCKED + lease, so crashed workers release work."""

import json

import psycopg

MAX_ATTEMPTS = 4


def enqueue(
    conn: psycopg.Connection,
    kind: str,
    dedup_key: str,
    payload: dict,
    refresh_after: str = "7 days",
    delay: str = "0 seconds",
) -> None:
    """Insert a job, or re-arm a finished one if it is older than `refresh_after` (periodic refresh)."""
    conn.execute(
        """
        INSERT INTO jobs (kind, dedup_key, payload, run_after)
        VALUES (%s, %s, %s, now() + %s::interval)
        ON CONFLICT (dedup_key) DO UPDATE
            SET status = 'pending', attempts = 0, payload = excluded.payload, run_after = excluded.run_after,
                last_error = NULL, updated_at = now()
            WHERE jobs.status = 'done' AND jobs.updated_at < now() - %s::interval
        """,
        (kind, dedup_key, json.dumps(payload, ensure_ascii=False), delay, refresh_after),
    )


def claim(conn: psycopg.Connection, kinds: list[str], lease: str = "10 minutes") -> dict | None:
    with conn.transaction():
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE kind = ANY(%s) AND run_after <= now()
              AND (status IN ('pending', 'retry_pending') OR (status = 'running' AND lease_until < now()))
            ORDER BY run_after, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (kinds,),
        ).fetchone()
        if not row:
            return None
        return conn.execute(
            """
            UPDATE jobs SET status = 'running', attempts = attempts + 1, lease_until = now() + %s::interval,
                            updated_at = now()
            WHERE id = %s RETURNING *
            """,
            (lease, row["id"]),
        ).fetchone()


def complete(conn: psycopg.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE jobs SET status = 'done', lease_until = NULL, last_error = NULL, updated_at = now() WHERE id = %s",
        (job_id,),
    )
    conn.commit()


def fail(conn: psycopg.Connection, job: dict, error: str, retryable: bool) -> None:
    """Network errors -> retry with backoff; repeated/non-retryable -> review_required (not 'company closed')."""
    if retryable and job["attempts"] < MAX_ATTEMPTS:
        status, delay = "retry_pending", f"{5 * 4 ** job['attempts']} minutes"
    else:
        status, delay = "review_required", "0 seconds"
    conn.execute(
        """
        UPDATE jobs SET status = %s, run_after = now() + %s::interval, lease_until = NULL, last_error = %s,
                        updated_at = now()
        WHERE id = %s
        """,
        (status, delay, error[:500], job["id"]),
    )
    conn.commit()


def defer(conn: psycopg.Connection, job_id: int, delay: str) -> None:
    """Re-schedule a job that is waiting on something external (a deferred search); not counted as a failure."""
    conn.execute(
        """
        UPDATE jobs SET status = 'pending', attempts = greatest(attempts - 1, 0), run_after = now() + %s::interval,
                        lease_until = NULL, updated_at = now()
        WHERE id = %s
        """,
        (delay, job_id),
    )
    conn.commit()
