# Data card: synthetic student data

| Item | Value |
|---|---|
| Purpose | Test and demonstrate attendance, results, exam eligibility and placement tools without real personal data |
| Generator | `scripts/generate_students.py` (Python, fixed seed 2026; deterministic) |
| LLM used for generation | None so far. **To do:** add an LLM-assisted generation step and store its verbatim prompts (the guide scores this) |
| Schema | Fixed hackathon schema: `students`, `courses`, `attendance`, `results` (`app/db/schema.sql`) |
| Validation | `scripts/validate_students.py` → `data/synthetic/validation_report.json` (0 invalid rows on 2026-10-06) |
| Row counts | 40 students, 6 courses, 65 attendance rows, 63 result rows |
| Distribution | 2 programmes (B.Tech CSE, B.Tech ECE) × 2 batches (2024, 2025); 40 classes held per course |
| Names | Invented first/last name combinations; no real people |
| Reserved ranges avoided | Student IDs S9000-S9999 and course codes `JDG*` (judges) |

## Deliberate edge cases (aligned with `data/rules.csv`)

| Student | Case |
|---|---|
| S1001 | CS201 attendance exactly 80.0% (32/40), the current minimum |
| S1002 | CS201 attendance 77.5% (31/40), one class below 80% but above the old 75% |
| S1003 | CS201 FAIL with 39/100, one mark below the pass mark |
| S1004 | ABSENT in CS203 |
| S1005 | DETAINED in CS201 (50% attendance) |
| S1006 | Two active backlogs |
| S1007 | CGPA exactly 6.50, the placement cut-off |
| S1008 | One backlog (CS301); what-if "pass the supplementary" → placement eligible |
| S1009 | Attendance but no results yet |
| S1010 | CGPA 6.49, just below the cut-off |
| S1011 | ECE student at exactly 75% (eligible under the old rule only) |

## Checks performed

attended ≤ held; held > 0; total = internal + external; 0 ≤ total ≤ max; CGPA 0-10; semester 1-10; backlogs ≥ 0; ID format `S` + 4 digits; unique keys; foreign keys to students and courses; `active_backlogs` equals the number of FAIL/ABSENT/DETAINED results.

## Known limitations

Results exist for one exam session (2026-MAY) only. Marks for random students are uniform within PASS/FAIL ranges. CGPA is not derived from marks.
