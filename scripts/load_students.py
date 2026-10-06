"""Load student CSVs (fixed hackathon schema) into SQLite. Judges use this for their own test students.

Expected files in --dir (any may be missing): students.csv, courses.csv, attendance.csv, results.csv.
Valid rows are inserted or updated; invalid rows are reported and skipped. Audit history is never touched.
The new students are answerable immediately, without restarting the API.

Passwords (for login):
- a student who has no password yet gets the demo password (DEMO_STUDENT_PASSWORD, or --password);
- an optional credentials.csv (student_id,password) in --dir sets a password per student;
- reloading a student keeps their existing password. Only bcrypt hashes are stored, never the password.

Usage: python scripts/load_students.py --dir test_students/ [--password <demo password>]
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402
from app.security.auth import hash_password  # noqa: E402
from app.services.data_validation import INT_COLUMNS, TABLES, read_tables, validate  # noqa: E402


def _convert(row: dict) -> tuple:
    return tuple(int(v) if k in INT_COLUMNS else (float(v) if k == "cgpa" else v) for k, v in row.items())


def upsert_sql(table: str, cols: list[str]) -> str:
    marks = ", ".join("?" for _ in cols)
    if table != "students":
        return f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({marks})"
    # Students are updated in place so their password_hash (not in the CSV) is kept.
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "student_id")
    return f"INSERT INTO students ({', '.join(cols)}) VALUES ({marks}) ON CONFLICT(student_id) DO UPDATE SET {updates}"


def set_passwords(directory: Path, loaded_ids: list[str], default_password: str) -> int:
    """Give loaded students without a password the default one; apply credentials.csv if present."""
    explicit = {}
    creds = directory / "credentials.csv"
    if creds.exists():
        with creds.open(newline="") as fh:
            explicit = {r["student_id"].strip().upper(): r["password"] for r in csv.DictReader(fh) if r.get("password")}
    changed = 0
    for sid in loaded_ids:
        if sid in explicit:
            database.set_password_hash(sid, hash_password(explicit[sid]))
            changed += 1
        elif database.get_password_hash(sid) is None:
            database.set_password_hash(sid, hash_password(default_password))
            changed += 1
    return changed


def load(directory: Path, default_password: str | None = None) -> int:
    database.init_db()
    tables, errors = read_tables(directory)
    for e in errors:
        print(f"[error] {e}")
    existing_students = {r["student_id"] for r in database.fetch_all("SELECT student_id FROM students")}
    existing_courses = {r["course_code"] for r in database.fetch_all("SELECT course_code FROM courses")}
    report = validate(tables, existing_students, existing_courses)

    bad = len(errors)
    with database.connect() as conn:
        for table in ["students", "courses", "attendance", "results"]:  # parents before children
            loaded = 0
            cols = TABLES[table]
            sql = upsert_sql(table, cols)
            for row_no, row, problems in report[table]:
                if problems:
                    print(f"[invalid] {table}.csv row {row_no}: {'; '.join(problems)}")
                    bad += 1
                    continue
                conn.execute(sql, _convert(row))
                loaded += 1
            print(f"[loaded ] {table}: {loaded} row(s)")
    loaded_ids = [row["student_id"] for _, row, problems in report["students"] if not problems]
    changed = set_passwords(directory, loaded_ids, default_password or settings.demo_student_password)
    print(f"[password] {changed} student(s) given a password hash (existing passwords kept)")
    print("done" if not bad else f"done; {bad} invalid row(s) skipped")
    return 1 if bad else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    parser.add_argument("--password", help="for loaded students without one (default: DEMO_STUDENT_PASSWORD)")
    args = parser.parse_args()
    return load(Path(args.dir), args.password)


if __name__ == "__main__":
    sys.exit(main())
