"""Deterministic tools against the synthetic data and the sample rules."""

from app.db import database
from app.retrieval import store
from app.tools import student_tools as tools


def ctx(student_id: str, as_of: str = "2026-10-06") -> tools.ToolContext:
    hits = store.search("minimum attendance required to appear in the end-semester examination")
    return tools.ToolContext(student_id, database.get_student(student_id), as_of, hits)


def test_attendance_is_calculated_by_code():
    out = tools.get_attendance(ctx("S1002"), "CS201")
    assert (out["classes_attended"], out["classes_held"], out["attendance_pct"]) == (31, 40, 77.5)


def test_attendance_no_record():
    assert tools.get_attendance(ctx("S1011"), "CS201")["status"] == "no_record"


def test_exactly_at_threshold_is_eligible_and_one_class_below_is_not():
    assert tools.check_exam_eligibility(ctx("S1001"), "CS201")["result"] == "ELIGIBLE"  # 32/40 = 80%
    assert tools.check_exam_eligibility(ctx("S1002"), "CS201")["result"] == "NOT_ELIGIBLE"  # 31/40


def test_rule_depends_on_date():
    # Before the circular (1 Aug 2026) the regulation's 75% applies, so 77.5% is enough.
    out = tools.check_exam_eligibility(ctx("S1002", "2026-07-15"), "CS201")
    assert out["result"] == "ELIGIBLE"
    assert out["rules"][0]["rule_id"] == "ATT-MIN-01"


def test_threshold_comes_from_the_rule_registry_not_code():
    with database.connect() as conn:
        conn.execute("UPDATE rule_registry SET value = '85' WHERE rule_id = 'ATT-MIN-02'")
    try:
        out = tools.check_exam_eligibility(ctx("S1001"), "CS201")
        assert out["result"] == "NOT_ELIGIBLE"
        assert out["checks"][0]["required"] == ">= 85%"
    finally:
        with database.connect() as conn:
            conn.execute("UPDATE rule_registry SET value = '80' WHERE rule_id = 'ATT-MIN-02'")


def test_detained_student_is_not_eligible():
    out = tools.check_exam_eligibility(ctx("S1005"), "CS201")
    assert out["result"] == "NOT_ELIGIBLE"
    assert any(c["actual"] == "DETAINED" for c in out["checks"])


def test_supplementary_eligibility():
    assert tools.check_exam_eligibility(ctx("S1003"), "CS201", "SUPPLEMENTARY")["result"] == "ELIGIBLE"  # FAIL
    assert tools.check_exam_eligibility(ctx("S1004"), "CS203", "SUPPLEMENTARY")["result"] == "ELIGIBLE"  # ABSENT
    assert tools.check_exam_eligibility(ctx("S1005"), "CS201", "SUPPLEMENTARY")["result"] == "NOT_ELIGIBLE"
    assert tools.check_exam_eligibility(ctx("S1001"), "CS201", "SUPPLEMENTARY")["result"] == "NOT_ELIGIBLE"


def test_placement_boundaries():
    assert tools.check_placement_eligibility(ctx("S1007"))["result"] == "ELIGIBLE"  # CGPA exactly 6.50
    assert tools.check_placement_eligibility(ctx("S1010"))["result"] == "NOT_ELIGIBLE"  # 6.49
    assert tools.check_placement_eligibility(ctx("S1006"))["result"] == "NOT_ELIGIBLE"  # 2 backlogs


def test_what_if_does_not_change_the_database():
    before = database.get_student("S1008")
    out = tools.check_placement_eligibility(ctx("S1008"), assume_pass="CS301")
    assert out["result"] == "ELIGIBLE"
    assert any("CGPA is kept" in a for a in out["assumptions"])
    assert database.get_student("S1008") == before


def test_hypothetical_attendance():
    assert tools.check_exam_eligibility(ctx("S1001"), None, hypothetical_pct=68)["result"] == "NOT_ELIGIBLE"
    assert tools.check_exam_eligibility(ctx("S1001"), None, hypothetical_pct=80)["result"] == "ELIGIBLE"


def test_tools_take_no_student_id_argument():
    import inspect

    for fn in (tools.get_attendance, tools.get_results, tools.get_rule, tools.check_exam_eligibility,
               tools.check_placement_eligibility):
        assert "student_id" not in inspect.signature(fn).parameters
