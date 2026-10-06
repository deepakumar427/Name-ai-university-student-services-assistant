"""Generate synthetic student data (fixed hackathon schema) into data/synthetic/*.csv.

- Fixed seed, so every run produces the same data.
- A short list of hand-placed edge-case students sits exactly on the thresholds in data/rules.csv.
- All names are invented. No real student data is used anywhere.
- IDs S9000-S9999 and course codes starting with JDG are reserved for judges and never used here.

Usage: python scripts/generate_students.py [--out data/synthetic]
"""

import argparse
import csv
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = 2026
SESSION = "2026-MAY"

FIRST = ["Aarav", "Diya", "Kabir", "Ananya", "Vihaan", "Ishita", "Arjun", "Meera", "Rohan", "Saanvi",
         "Aditya", "Tara", "Kunal", "Nisha", "Dev", "Riya", "Yash", "Pooja", "Neel", "Kavya"]
LAST = ["Sharma", "Verma", "Iyer", "Khan", "Gupta", "Reddy", "Mehta", "Das", "Nair", "Singh"]

COURSES = [
    # course_code, course_name, programme, semester, credits
    ("CS201", "Data Structures", "B.Tech CSE", 3, 4),
    ("CS203", "Discrete Mathematics", "B.Tech CSE", 3, 3),
    ("CS301", "Operating Systems", "B.Tech CSE", 5, 4),
    ("CS303", "Database Management Systems", "B.Tech CSE", 5, 4),
    ("EC201", "Signals and Systems", "B.Tech ECE", 3, 4),
    ("EC301", "Digital Communication", "B.Tech ECE", 5, 4),
]

# programme, batch_year -> (current_semester, courses)
COHORTS = {
    ("B.Tech CSE", 2025): (3, ["CS201", "CS203"]),
    ("B.Tech CSE", 2024): (5, ["CS301", "CS303"]),
    ("B.Tech ECE", 2025): (3, ["EC201"]),
    ("B.Tech ECE", 2024): (5, ["EC301"]),
}

HELD = 40  # classes held per course; 32/40 = 80.0% sits exactly on the current minimum


def marks_for(result: str, rng: random.Random) -> tuple[int, int]:
    """Return (internal, external) consistent with the result (pass mark 40/100)."""
    if result == "PASS":
        total = rng.randint(45, 92)
    elif result == "FAIL":
        total = rng.randint(20, 38)
    else:  # ABSENT or DETAINED: no end-semester marks
        internal = rng.randint(10, 30)
        return internal, 0
    internal = min(40, max(10, round(total * 0.4) + rng.randint(-3, 3)))
    return internal, total - internal


# Edge cases: (student_id, programme, batch, cgpa, {course: (attended, result)}, note)
EDGE_CASES = [
    ("S1001", "B.Tech CSE", 2025, 7.80, {"CS201": (32, "PASS"), "CS203": (36, "PASS")}, "CS201 attendance exactly 80.0%"),
    ("S1002", "B.Tech CSE", 2025, 7.10, {"CS201": (31, "PASS"), "CS203": (35, "PASS")}, "CS201 attendance 77.5%, one class below 80%"),
    ("S1003", "B.Tech CSE", 2025, 6.20, {"CS201": (36, "FAIL:39"), "CS203": (34, "PASS")}, "CS201 failed with 39/100, one mark below pass"),
    ("S1004", "B.Tech CSE", 2025, 6.90, {"CS201": (35, "PASS"), "CS203": (33, "ABSENT")}, "absent in CS203"),
    ("S1005", "B.Tech CSE", 2025, 5.60, {"CS201": (20, "DETAINED"), "CS203": (33, "PASS")}, "detained in CS201 (50% attendance)"),
    ("S1006", "B.Tech CSE", 2024, 5.90, {"CS301": (34, "FAIL"), "CS303": (33, "FAIL")}, "two active backlogs"),
    ("S1007", "B.Tech CSE", 2024, 6.50, {"CS301": (38, "PASS"), "CS303": (37, "PASS")}, "CGPA exactly at the 6.5 placement cut-off"),
    ("S1008", "B.Tech CSE", 2024, 7.20, {"CS301": (36, "FAIL"), "CS303": (38, "PASS")}, "one backlog; what-if pass -> placement eligible"),
    ("S1009", "B.Tech CSE", 2024, 8.10, {"CS301": (39, None), "CS303": (37, None)}, "attendance but no results yet"),
    ("S1010", "B.Tech CSE", 2024, 6.49, {"CS301": (35, "PASS"), "CS303": (36, "PASS")}, "CGPA 6.49, just below the cut-off"),
    ("S1011", "B.Tech ECE", 2025, 7.40, {"EC201": (30, "PASS")}, "ECE student, 75.0% attendance (meets old rule, not new)"),
]


def build(rng: random.Random):
    students, attendance, results, notes = [], [], [], {}
    used_names = set()

    def name() -> str:
        while True:
            n = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if n not in used_names:
                used_names.add(n)
                return n

    def add_student(sid, programme, batch, cgpa, course_plan):
        semester, _ = COHORTS[(programme, batch)]
        backlogs = 0
        for course, (attended, result) in course_plan.items():
            attendance.append([sid, course, HELD, attended])
            if result is None:
                continue
            fixed_total = None
            if ":" in result:
                result, fixed_total = result.split(":")[0], int(result.split(":")[1])
            if fixed_total is not None:
                internal = 16
                external = fixed_total - internal
            else:
                internal, external = marks_for(result, rng)
            results.append([sid, course, SESSION, "REGULAR", internal, external, internal + external, 100, result])
            backlogs += result in {"FAIL", "ABSENT", "DETAINED"}
        students.append([sid, name(), programme, batch, semester, f"{cgpa:.2f}", backlogs])

    for sid, programme, batch, cgpa, plan, note in EDGE_CASES:
        add_student(sid, programme, batch, cgpa, plan)
        notes[sid] = note

    cohorts = list(COHORTS)
    for i in range(12, 41):  # S1012 .. S1040, spread across the four cohorts
        programme, batch = cohorts[i % len(cohorts)]
        plan = {}
        for course in COHORTS[(programme, batch)][1]:
            attended = rng.randint(33, 40)  # comfortably above 80%
            result = rng.choices(["PASS", "FAIL"], weights=[9, 1])[0]
            plan[course] = (attended, result)
        add_student(f"S{1000 + i}", programme, batch, round(rng.uniform(5.8, 9.4), 2), plan)
    return students, attendance, results, notes


def write(out: Path, name: str, header: list[str], rows: list[list]) -> None:
    with (out / name).open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "data" / "synthetic"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    students, attendance, results, notes = build(random.Random(SEED))
    write(out, "students.csv", ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa",
                                "active_backlogs"], students)
    write(out, "courses.csv", ["course_code", "course_name", "programme", "semester", "credits"],
          [list(c) for c in COURSES])
    write(out, "attendance.csv", ["student_id", "course_code", "classes_held", "classes_attended"], attendance)
    write(out, "results.csv", ["student_id", "course_code", "exam_session", "exam_type", "internal_marks",
                               "external_marks", "total_marks", "max_marks", "result"], results)
    write(out, "edge_cases.csv", ["student_id", "edge_case"], [[k, v] for k, v in notes.items()])
    print(f"wrote {len(students)} students, {len(COURSES)} courses, {len(attendance)} attendance rows, "
          f"{len(results)} result rows to {out}")


if __name__ == "__main__":
    main()
