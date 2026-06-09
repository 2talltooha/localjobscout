from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_DB_PATH: Path | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    title               TEXT NOT NULL,
    company             TEXT,
    location            TEXT,
    url                 TEXT NOT NULL,
    description         TEXT NOT NULL,
    posted_at           TEXT,
    first_seen          TEXT NOT NULL,
    score               REAL,
    notified            INTEGER DEFAULT 0,
    salary_min          INTEGER,
    salary_max          INTEGER,
    job_type            TEXT,
    skills              TEXT,
    job_hash            TEXT,
    application_status  TEXT,
    applied_at          TEXT,
    cover_letter_path   TEXT,
    application_notes   TEXT,
    deadline            TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen);
CREATE INDEX IF NOT EXISTS idx_jobs_notified   ON jobs(notified);
CREATE INDEX IF NOT EXISTS idx_jobs_job_hash   ON jobs(job_hash);

CREATE TABLE IF NOT EXISTS job_hashes (
    job_hash   TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    posted_at  TEXT,
    first_seen TEXT NOT NULL,
    last_alert TEXT
);

CREATE TABLE IF NOT EXISTS alerts_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_sent_at TEXT NOT NULL,
    matched_count INTEGER NOT NULL,
    jobs_alerted  TEXT NOT NULL,
    filter_used   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gap_cache (
    job_id      TEXT NOT NULL,
    master_hash TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (job_id, master_hash)
);
"""

_COLUMN_MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN salary_min INTEGER",
    "ALTER TABLE jobs ADD COLUMN salary_max INTEGER",
    "ALTER TABLE jobs ADD COLUMN job_type TEXT",
    "ALTER TABLE jobs ADD COLUMN skills TEXT",
    "ALTER TABLE jobs ADD COLUMN job_hash TEXT",
    "ALTER TABLE jobs ADD COLUMN application_status TEXT",
    "ALTER TABLE jobs ADD COLUMN applied_at TEXT",
    "ALTER TABLE jobs ADD COLUMN cover_letter_path TEXT",
    "ALTER TABLE jobs ADD COLUMN application_notes TEXT",
    "ALTER TABLE jobs ADD COLUMN suitability_score REAL",
    "ALTER TABLE jobs ADD COLUMN suitability_reason TEXT",
    "ALTER TABLE jobs ADD COLUMN deadline TEXT",
]

_INDEX_MIGRATIONS = [
    (
        "CREATE INDEX IF NOT EXISTS idx_jobs_app_status "
        "ON jobs(application_status)"
    ),
]


APPLICATION_STATUSES = (
    "seen",
    "interested",
    "applied",
    "interviewed",
    "rejected",
    "offered",
    "hidden",
)


@dataclass
class Job:
    id: str
    source: str
    title: str
    url: str
    description: str
    company: str = ""
    location: str = ""
    posted_at: str | None = None
    first_seen: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    score: float | None = None
    notified: bool = False
    salary_min: int | None = None
    salary_max: int | None = None
    job_type: str = ""
    skills: list[str] = field(default_factory=list)
    job_hash: str = ""
    application_status: str | None = None
    applied_at: str | None = None
    cover_letter_path: str | None = None
    application_notes: str | None = None
    suitability_score: float | None = None
    suitability_reason: str | None = None
    deadline: str | None = None


def make_job_id(source: str, url: str) -> str:
    return hashlib.sha256(f"{source}:{url}".encode()).hexdigest()


def _require_db() -> Path:
    path = _DB_PATH
    if path is None:
        raise RuntimeError("Call init_db() before using database functions.")
    return path


@contextmanager
def _get_conn(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """Initialise the database and set the module-level path for all other functions."""
    global _DB_PATH
    _DB_PATH = db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _get_conn(db_path) as conn:
        conn.executescript(_SCHEMA)
        for stmt in _COLUMN_MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        for stmt in _INDEX_MIGRATIONS:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # missing column / already exists


def upsert_job(job: Job) -> bool:
    """Insert *job* if its id is not already present. Returns True on a new insert."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO jobs
                (id, source, title, company, location, url, description,
                 posted_at, first_seen, score, notified,
                 salary_min, salary_max, job_type, skills, job_hash, deadline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.source,
                job.title,
                job.company or None,
                job.location or None,
                job.url,
                job.description,
                job.posted_at,
                job.first_seen,
                job.score,
                int(job.notified),
                job.salary_min,
                job.salary_max,
                job.job_type or None,
                json.dumps(job.skills) if job.skills else None,
                job.job_hash or None,
                job.deadline,
            ),
        )
        return cursor.rowcount == 1


def update_score(job_id: str, score: float) -> None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute("UPDATE jobs SET score = ? WHERE id = ?", (score, job_id))


def update_description(job_id: str, description: str) -> None:
    """Replace a job's stored description (e.g. after fetching the full posting
    to supersede a truncated API snippet)."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET description = ? WHERE id = ?", (description, job_id)
        )


def mark_notified(job_id: str) -> None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute("UPDATE jobs SET notified = 1 WHERE id = ?", (job_id,))


def get_unnotified_above(threshold: float) -> list[Job]:
    """Return jobs with score >= threshold that have not yet triggered a notif."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            SELECT * FROM jobs
            WHERE notified = 0 AND score >= ?
            ORDER BY first_seen DESC
            """,
            (threshold,),
        )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def get_recent_jobs(limit: int | None = 50) -> list[Job]:
    """Return the most recently first_seen jobs, regardless of score or notified
    status. Pass limit=None to return the full corpus."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        if limit is None:
            cursor = conn.execute(
                "SELECT * FROM jobs ORDER BY first_seen DESC"
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM jobs ORDER BY first_seen DESC LIMIT ?",
                (limit,),
            )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def get_all_for_rescore() -> list[Job]:
    """Return all non-excluded jobs (score != -1.0) for rescoring against a
    new or updated resume."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE score IS NULL OR score > -0.5 "
            "ORDER BY first_seen DESC"
        )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def hash_exists_in_db(job_hash: str) -> bool:
    """Return True if any job with this content hash already exists in the DB
    (cross-source duplicate detection)."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT 1 FROM jobs WHERE job_hash = ? LIMIT 1", (job_hash,)
        )
        return cursor.fetchone() is not None


def add_job_hash(job_hash: str, source: str, posted_at: str | None) -> bool:
    """Record a job hash. Returns True if new, False if already present."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO job_hashes (job_hash, source, posted_at, first_seen)
            VALUES (?, ?, ?, ?)
            """,
            (job_hash, source, posted_at, datetime.now(UTC).isoformat()),
        )
        return cursor.rowcount == 1


def mark_hash_alerted(job_hash: str) -> None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE job_hashes SET last_alert = ? WHERE job_hash = ?",
            (datetime.now(UTC).isoformat(), job_hash),
        )


def log_alert(matched_count: int, job_ids: list[str], filter_used: str) -> None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO alerts_log
                (alert_sent_at, matched_count, jobs_alerted, filter_used)
            VALUES (?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).isoformat(),
                matched_count,
                json.dumps(job_ids),
                filter_used,
            ),
        )


def _row_to_job(row: sqlite3.Row) -> Job:
    keys = row.keys()
    skills_raw = row["skills"] if "skills" in keys else None
    skills: list[str] = json.loads(skills_raw) if skills_raw else []
    return Job(
        id=row["id"],
        source=row["source"],
        title=row["title"],
        company=row["company"] or "",
        location=row["location"] or "",
        url=row["url"],
        description=row["description"],
        posted_at=row["posted_at"],
        first_seen=row["first_seen"],
        score=row["score"],
        notified=bool(row["notified"]),
        salary_min=row["salary_min"] if "salary_min" in keys else None,
        salary_max=row["salary_max"] if "salary_max" in keys else None,
        job_type=row["job_type"] or "" if "job_type" in keys else "",
        skills=skills,
        job_hash=row["job_hash"] or "" if "job_hash" in keys else "",
        application_status=(
            row["application_status"]
            if "application_status" in keys
            else None
        ),
        applied_at=row["applied_at"] if "applied_at" in keys else None,
        cover_letter_path=(
            row["cover_letter_path"]
            if "cover_letter_path" in keys
            else None
        ),
        application_notes=(
            row["application_notes"]
            if "application_notes" in keys
            else None
        ),
        suitability_score=(
            float(row["suitability_score"])
            if "suitability_score" in keys and row["suitability_score"] is not None
            else None
        ),
        suitability_reason=(
            row["suitability_reason"]
            if "suitability_reason" in keys
            else None
        ),
        deadline=row["deadline"] if "deadline" in keys else None,
    )


def get_job_by_id(job_id: str) -> Job | None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
    return _row_to_job(row) if row else None


def find_job_by_short_id(prefix: str) -> Job | None:
    """Find a single job whose id starts with `prefix`. Returns None if no
    match or more than one match."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT * FROM jobs WHERE id LIKE ? LIMIT 2",
            (f"{prefix}%",),
        )
        rows = cursor.fetchall()
    if len(rows) != 1:
        return None
    return _row_to_job(rows[0])


def update_application_status(
    job_id: str,
    status: str | None,
    *,
    notes: str | None = None,
) -> None:
    """Set application_status; stamp applied_at when status == 'applied'.
    Pass status=None to clear tracking on a job."""
    db_path = _require_db()
    applied_at = datetime.now(UTC).isoformat() if status == "applied" else None
    with _get_conn(db_path) as conn:
        if status is None:
            conn.execute(
                """UPDATE jobs SET application_status = NULL,
                                   applied_at = NULL,
                                   application_notes = NULL
                   WHERE id = ?""",
                (job_id,),
            )
            return
        if applied_at is not None:
            conn.execute(
                """UPDATE jobs
                   SET application_status = ?,
                       applied_at = ?,
                       application_notes = COALESCE(?, application_notes)
                   WHERE id = ?""",
                (status, applied_at, notes, job_id),
            )
        else:
            conn.execute(
                """UPDATE jobs
                   SET application_status = ?,
                       application_notes = COALESCE(?, application_notes)
                   WHERE id = ?""",
                (status, notes, job_id),
            )


def set_cover_letter_path(job_id: str, path: str) -> None:
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET cover_letter_path = ? WHERE id = ?",
            (path, job_id),
        )


def get_suitability(job_id: str) -> tuple[float, str] | None:
    """Return cached (score, reason) for job_id, or None if not yet scored."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT suitability_score, suitability_reason FROM jobs "
            "WHERE id = ? AND suitability_score IS NOT NULL",
            (job_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return float(row[0]), str(row[1] or "")


def set_suitability(job_id: str, score: float, reason: str) -> None:
    """Store suitability score + reason for job_id."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            "UPDATE jobs SET suitability_score = ?, suitability_reason = ? "
            "WHERE id = ?",
            (score, reason, job_id),
        )


def get_jobs_for_suitability(threshold: float, limit: int = 50) -> list[Job]:
    """Return jobs above threshold that have not yet been suitability-scored."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """SELECT * FROM jobs
               WHERE score >= ?
                 AND score > -0.5
                 AND suitability_score IS NULL
               ORDER BY score DESC
               LIMIT ?""",
            (threshold, limit),
        )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def get_manual_queue_jobs(
    threshold: float,
    status_filter: str | None = None,
    today: str | None = None,
    min_date: str | None = None,
) -> list[Job]:
    """Return jobs suitable for the manual-submit queue.

    Excludes applied / rejected / hidden jobs.  With status_filter set,
    returns only that status (e.g. 'interested').  Without it, returns all
    non-terminal statuses (None, 'seen', 'interested').

    Freshness filters (both optional, applied when supplied):
    - ``today`` (YYYY-MM-DD): hide jobs whose ``deadline`` has already passed.
    - ``min_date`` (YYYY-MM-DD): hide jobs older than this, comparing the
      posting date — ``posted_at`` when present, else ``first_seen``.
    """
    db_path = _require_db()
    excluded_statuses = ("applied", "rejected", "hidden")

    fresh_sql = ""
    fresh_params: list[str] = []
    if today is not None:
        fresh_sql += " AND (deadline IS NULL OR deadline = '' OR deadline >= ?)"
        fresh_params.append(today)
    if min_date is not None:
        fresh_sql += " AND COALESCE(NULLIF(posted_at, ''), first_seen) >= ?"
        fresh_params.append(min_date)

    with _get_conn(db_path) as conn:
        if status_filter is not None:
            cursor = conn.execute(
                f"""SELECT * FROM jobs
                   WHERE score >= ?
                     AND score > -0.5
                     AND application_status = ?
                     {fresh_sql}
                   ORDER BY score DESC""",
                (threshold, status_filter, *fresh_params),
            )
        else:
            placeholders = ",".join("?" * len(excluded_statuses))
            cursor = conn.execute(
                f"""SELECT * FROM jobs
                   WHERE score >= ?
                     AND score > -0.5
                     AND (application_status IS NULL
                          OR application_status NOT IN ({placeholders}))
                     {fresh_sql}
                   ORDER BY score DESC""",
                (threshold, *excluded_statuses, *fresh_params),
            )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def get_jobs_with_deadlines(
    on_or_after: str | None = None,
    exclude_statuses: tuple[str, ...] = ("applied", "rejected", "hidden"),
) -> list[Job]:
    """Return non-excluded jobs that have a deadline, soonest first.

    Pass on_or_after='YYYY-MM-DD' to keep only deadlines on/after that date
    (e.g. today, to hide expired ones).
    """
    db_path = _require_db()
    placeholders = ",".join("?" * len(exclude_statuses))
    sql = (
        "SELECT * FROM jobs "
        "WHERE deadline IS NOT NULL "
        f"AND (application_status IS NULL "
        f"     OR application_status NOT IN ({placeholders}))"
    )
    params: list[str] = list(exclude_statuses)
    if on_or_after is not None:
        sql += " AND deadline >= ?"
        params.append(on_or_after)
    sql += " ORDER BY deadline ASC"
    with _get_conn(db_path) as conn:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]


def get_gap_report(job_id: str, master_hash: str) -> str | None:
    """Return cached gap-analysis JSON for (job_id, master_hash), or None.

    The master_hash component means a changed master resume automatically
    invalidates stale reports — a new hash simply misses the cache.
    """
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            "SELECT report_json FROM gap_cache "
            "WHERE job_id = ? AND master_hash = ?",
            (job_id, master_hash),
        )
        row = cursor.fetchone()
    return str(row[0]) if row is not None else None


def set_gap_report(job_id: str, master_hash: str, report_json: str) -> None:
    """Store gap-analysis JSON for (job_id, master_hash)."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO gap_cache
                   (job_id, master_hash, report_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (job_id, master_hash, report_json, datetime.now(UTC).isoformat()),
        )


def get_applied_jobs(status: str | None = None) -> list[Job]:
    """Return jobs with a non-null application_status, newest applied first.
    Pass status='applied' (etc.) to filter to one bucket."""
    db_path = _require_db()
    with _get_conn(db_path) as conn:
        if status is None:
            cursor = conn.execute(
                """SELECT * FROM jobs
                   WHERE application_status IS NOT NULL
                   ORDER BY applied_at DESC, first_seen DESC"""
            )
        else:
            cursor = conn.execute(
                """SELECT * FROM jobs
                   WHERE application_status = ?
                   ORDER BY applied_at DESC, first_seen DESC""",
                (status,),
            )
        rows = cursor.fetchall()
    return [_row_to_job(row) for row in rows]
