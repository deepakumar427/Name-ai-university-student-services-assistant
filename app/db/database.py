"""SQLite access. Every query is parameterised. Nothing here makes policy decisions."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Create tables if they don't exist, and add new columns to older databases. Never drops anything."""
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text())
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(students)")}
        if "password_hash" not in columns:
            conn.execute("ALTER TABLE students ADD COLUMN password_hash TEXT")


def fetch_all(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def fetch_one(sql: str, params: tuple = ()) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def ping() -> bool:
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True
    except sqlite3.Error:
        return False


# ---------- students and courses ----------

STUDENT_COLUMNS = "student_id, full_name, programme, batch_year, current_semester, cgpa, active_backlogs"


def get_student(student_id: str) -> dict | None:
    """Student profile. Never includes the password hash."""
    return fetch_one(f"SELECT {STUDENT_COLUMNS} FROM students WHERE student_id = ?", (student_id,))


def get_password_hash(student_id: str) -> str | None:
    row = fetch_one("SELECT password_hash FROM students WHERE student_id = ?", (student_id,))
    return row["password_hash"] if row else None


def set_password_hash(student_id: str, password_hash: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE students SET password_hash = ? WHERE student_id = ?", (password_hash, student_id))


def get_course(course_code: str) -> dict | None:
    return fetch_one("SELECT * FROM courses WHERE course_code = ?", (course_code,))


def all_courses() -> list[dict]:
    return fetch_all("SELECT * FROM courses ORDER BY course_code")


def student_courses(student_id: str) -> list[dict]:
    """Courses the student has attendance or results for."""
    return fetch_all(
        """SELECT DISTINCT c.course_code, c.course_name FROM courses c
           WHERE c.course_code IN (SELECT course_code FROM attendance WHERE student_id = ?)
              OR c.course_code IN (SELECT course_code FROM results WHERE student_id = ?)
           ORDER BY c.course_code""",
        (student_id, student_id),
    )


def get_attendance_row(student_id: str, course_code: str) -> dict | None:
    return fetch_one(
        "SELECT classes_held, classes_attended FROM attendance WHERE student_id = ? AND course_code = ?",
        (student_id, course_code),
    )


def get_result_rows(student_id: str, course_code: str | None = None) -> list[dict]:
    sql = (
        "SELECT course_code, exam_session, exam_type, total_marks, max_marks, result "
        "FROM results WHERE student_id = ?"
    )
    params: tuple = (student_id,)
    if course_code:
        sql += " AND course_code = ?"
        params += (course_code,)
    return fetch_all(sql + " ORDER BY course_code, exam_session, exam_type", params)


# ---------- rules and documents ----------

def rules_for_parameter(parameter: str) -> list[dict]:
    return fetch_all("SELECT * FROM rule_registry WHERE parameter = ?", (parameter,))


def all_documents() -> list[dict]:
    return fetch_all("SELECT * FROM source_register ORDER BY authority_level, effective_from")


def get_document(doc_id: str) -> dict | None:
    return fetch_one("SELECT * FROM source_register WHERE doc_id = ?", (doc_id,))


def find_document_by_hash(content_hash: str) -> dict | None:
    return fetch_one("SELECT doc_id FROM source_register WHERE content_hash = ?", (content_hash,))


def insert_document(meta: dict) -> None:
    cols = ", ".join(meta)
    marks = ", ".join("?" for _ in meta)
    with connect() as conn:
        conn.execute(f"INSERT INTO source_register ({cols}) VALUES ({marks})", tuple(meta.values()))


def upsert_rule(rule: dict) -> None:
    cols = ", ".join(rule)
    marks = ", ".join("?" for _ in rule)
    with connect() as conn:
        conn.execute(f"INSERT OR REPLACE INTO rule_registry ({cols}) VALUES ({marks})", tuple(rule.values()))


# ---------- audit ----------

def insert_audit(record: dict) -> None:
    cols = ", ".join(record)
    marks = ", ".join("?" for _ in record)
    with connect() as conn:
        conn.execute(f"INSERT INTO audit_log ({cols}) VALUES ({marks})", tuple(record.values()))


def get_audit(trace_id: str) -> dict | None:
    return fetch_one("SELECT * FROM audit_log WHERE trace_id = ?", (trace_id,))


# ---------- authentication ----------

def revoke_token(jti: str, expires_at: int) -> None:
    with connect() as conn:
        conn.execute("INSERT OR IGNORE INTO revoked_tokens (jti, expires_at) VALUES (?, ?)", (jti, expires_at))
        conn.execute("DELETE FROM revoked_tokens WHERE expires_at < strftime('%s','now')")


def is_token_revoked(jti: str) -> bool:
    return fetch_one("SELECT 1 AS hit FROM revoked_tokens WHERE jti = ?", (jti,)) is not None


def log_security_event(event: str, student_id: str | None, detail: str = "") -> None:
    from datetime import datetime, timezone

    with connect() as conn:
        conn.execute(
            "INSERT INTO security_log (created_at, event, student_id, detail) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), event, student_id, detail[:200]),
        )
