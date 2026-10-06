"""Validate synthetic student CSVs and write data/synthetic/validation_report.json.

Usage: python scripts/validate_students.py [--dir data/synthetic]
Exit code 1 if any row is invalid.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.data_validation import cross_checks, read_tables, validate  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", default=str(ROOT / "data" / "synthetic"))
    args = parser.parse_args()
    directory = Path(args.dir)

    tables, errors = read_tables(directory)
    report = validate(tables)
    invalid = {t: [{"row": n, "problems": p} for n, _, p in rows if p] for t, rows in report.items()}
    warnings = cross_checks(tables)

    edge_file = directory / "edge_cases.csv"
    edge_cases = list(csv.DictReader(edge_file.open())) if edge_file.exists() else []
    students = {s["student_id"]: s for s in tables["students"]}
    attendance = {(a["student_id"], a["course_code"]): a for a in tables["attendance"]}
    edge_checks = {
        "S1001 CS201 = 80.0%": int(attendance[("S1001", "CS201")]["classes_attended"]) * 100
        / int(attendance[("S1001", "CS201")]["classes_held"]) == 80.0,
        "S1002 CS201 one class below 80%": int(attendance[("S1002", "CS201")]["classes_attended"])
        == int(attendance[("S1002", "CS201")]["classes_held"]) * 0.8 - 1,
        "S1007 CGPA = 6.50": float(students["S1007"]["cgpa"]) == 6.5,
        "S1006 has >= 2 backlogs": int(students["S1006"]["active_backlogs"]) >= 2,
    }

    summary = {
        "row_counts": {t: len(r) for t, r in tables.items()},
        "programmes": sorted({s["programme"] for s in tables["students"]}),
        "batches": sorted({s["batch_year"] for s in tables["students"]}),
        "file_errors": errors,
        "invalid_rows": invalid,
        "warnings": warnings,
        "edge_cases": edge_cases,
        "edge_case_checks": edge_checks,
    }
    out = directory / "validation_report.json"
    out.write_text(json.dumps(summary, indent=2))
    bad = sum(len(v) for v in invalid.values()) + len(errors) + sum(not ok for ok in edge_checks.values())
    print(json.dumps({k: summary[k] for k in ("row_counts", "programmes", "batches", "warnings", "edge_case_checks")},
                     indent=2))
    print(f"invalid rows/errors: {bad}  -> report written to {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
