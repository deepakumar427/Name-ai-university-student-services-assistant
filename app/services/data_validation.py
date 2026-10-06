"""Checks for student CSVs in the fixed hackathon schema. Used by the validator and the loader."""

import csv
import re
from pathlib import Path

TABLES = {
    "students": ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa", "active_backlogs"],
    "courses": ["course_code", "course_name", "programme", "semester", "credits"],
    "attendance": ["student_id", "course_code", "classes_held", "classes_attended"],
    "results": ["student_id", "course_code", "exam_session", "exam_type", "internal_marks", "external_marks",
                "total_marks", "max_marks", "result"],
}
INT_COLUMNS = {"batch_year", "current_semester", "active_backlogs", "semester", "credits", "classes_held",
               "classes_attended", "internal_marks", "external_marks", "total_marks", "max_marks"}


def read_tables(directory: Path) -> tuple[dict[str, list[dict]], list[str]]:
    tables, errors = {}, []
    for table, columns in TABLES.items():
        path = directory / f"{table}.csv"
        if not path.exists():
            tables[table] = []
            continue
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in columns if c not in (reader.fieldnames or [])]
            if missing:
                errors.append(f"{table}.csv is missing columns {missing}")
                tables[table] = []
                continue
            tables[table] = [{c: (row[c] or "").strip() for c in columns} for row in reader]
    return tables, errors


def _int(row: dict, col: str, problems: list[str]) -> int | None:
    try:
        return int(row[col])
    except ValueError:
        problems.append(f"{col} '{row[col]}' is not an integer")
        return None


def validate(tables: dict[str, list[dict]], existing_students: set[str] = frozenset(),
             existing_courses: set[str] = frozenset()) -> dict[str, list[tuple[int, dict, list[str]]]]:
    """Return {table: [(row_number, row, problems)]} for every row. Empty problems = valid."""
    report: dict[str, list] = {t: [] for t in TABLES}
    student_ids = set(existing_students) | {r["student_id"] for r in tables["students"]}
    course_codes = set(existing_courses) | {r["course_code"] for r in tables["courses"]}
    seen: dict[str, set] = {t: set() for t in TABLES}

    for i, row in enumerate(tables["students"], start=2):
        p: list[str] = []
        if not re.fullmatch(r"S\d{4}", row["student_id"]):
            p.append("student_id must be S + 4 digits")
        if row["student_id"] in seen["students"]:
            p.append("duplicate student_id")
        seen["students"].add(row["student_id"])
        sem = _int(row, "current_semester", p)
        if sem is not None and not 1 <= sem <= 10:
            p.append("current_semester must be 1-10")
        try:
            if not 0 <= float(row["cgpa"]) <= 10:
                p.append("cgpa must be 0.00-10.00")
        except ValueError:
            p.append("cgpa is not a number")
        backlogs = _int(row, "active_backlogs", p)
        if backlogs is not None and backlogs < 0:
            p.append("active_backlogs must be >= 0")
        _int(row, "batch_year", p)
        if not row["full_name"]:
            p.append("full_name is empty")
        report["students"].append((i, row, p))

    for i, row in enumerate(tables["courses"], start=2):
        p = []
        if row["course_code"] in seen["courses"]:
            p.append("duplicate course_code")
        seen["courses"].add(row["course_code"])
        _int(row, "semester", p)
        _int(row, "credits", p)
        report["courses"].append((i, row, p))

    for i, row in enumerate(tables["attendance"], start=2):
        p = []
        key = (row["student_id"], row["course_code"])
        if key in seen["attendance"]:
            p.append("duplicate (student_id, course_code)")
        seen["attendance"].add(key)
        if row["student_id"] not in student_ids:
            p.append("unknown student_id")
        if row["course_code"] not in course_codes:
            p.append("unknown course_code")
        held, attended = _int(row, "classes_held", p), _int(row, "classes_attended", p)
        if held is not None and held <= 0:
            p.append("classes_held must be > 0")
        if held is not None and attended is not None and not 0 <= attended <= held:
            p.append("classes_attended must be between 0 and classes_held")
        report["attendance"].append((i, row, p))

    for i, row in enumerate(tables["results"], start=2):
        p = []
        key = (row["student_id"], row["course_code"], row["exam_session"], row["exam_type"])
        if key in seen["results"]:
            p.append("duplicate (student_id, course_code, exam_session, exam_type)")
        seen["results"].add(key)
        if row["student_id"] not in student_ids:
            p.append("unknown student_id")
        if row["course_code"] not in course_codes:
            p.append("unknown course_code")
        if row["exam_type"] not in {"REGULAR", "SUPPLEMENTARY"}:
            p.append("exam_type must be REGULAR or SUPPLEMENTARY")
        if row["result"] not in {"PASS", "FAIL", "ABSENT", "DETAINED"}:
            p.append("result must be PASS, FAIL, ABSENT or DETAINED")
        internal, external = _int(row, "internal_marks", p), _int(row, "external_marks", p)
        total, maximum = _int(row, "total_marks", p), _int(row, "max_marks", p)
        if None not in (internal, external, total) and total != internal + external:
            p.append("total_marks must equal internal_marks + external_marks")
        if None not in (total, maximum) and not 0 <= total <= maximum:
            p.append("total_marks must be between 0 and max_marks")
        report["results"].append((i, row, p))
    return report


def cross_checks(tables: dict[str, list[dict]]) -> list[str]:
    """Softer consistency checks that are reported but don't block loading."""
    warnings = []
    programmes = {r["programme"] for r in tables["students"]}
    for c in tables["courses"]:
        if programmes and c["programme"] not in programmes:
            warnings.append(f"course {c['course_code']} programme '{c['programme']}' matches no student")
    failing: dict[str, int] = {}
    for r in tables["results"]:
        if r["result"] in {"FAIL", "ABSENT", "DETAINED"}:
            failing[r["student_id"]] = failing.get(r["student_id"], 0) + 1
    for s in tables["students"]:
        if s["active_backlogs"].isdigit() and int(s["active_backlogs"]) != failing.get(s["student_id"], 0):
            warnings.append(f"{s['student_id']}: active_backlogs={s['active_backlogs']} but "
                            f"{failing.get(s['student_id'], 0)} failing result(s)")
    for r in tables["students"]:
        if re.fullmatch(r"S9\d{3}", r["student_id"]):
            warnings.append(f"{r['student_id']} is in the judges' reserved range S9000-S9999")
    return warnings
