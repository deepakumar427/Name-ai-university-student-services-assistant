"""Answer templates for calculated results. Every number comes straight from a tool output."""

from app.db import database

RESULT_WORDS = {"ELIGIBLE": "Eligible", "NOT_ELIGIBLE": "Not eligible"}


def _course(code: str | None) -> str:
    if not code:
        return ""
    course = database.get_course(code)
    return f"{code} ({course['course_name']})" if course else code


def _rule_line(check: dict, cites: dict[str, str]) -> str:
    return f"Policy: {check['check']} must be {check['required']}{cites.get(check['rule_id'], '')}."


def attendance_answer(att: dict, rule: dict | None, cite: str) -> str:
    text = (f"Your attendance in {_course(att['course_code'])} is {att['attendance_pct']:g}% "
            f"({att['classes_attended']} of {att['classes_held']} classes attended).")
    if rule and rule.get("status") == "ok":
        meets = "meets" if rule["meets"] else "does not meet"
        text += f" The minimum required is {rule['operator']} {rule['value']}%{cite}, so this {meets} the requirement."
    return text


def exam_answer(out: dict, cites: dict[str, str]) -> str:
    kind = "supplementary" if out["exam_type"] == "SUPPLEMENTARY" else "end-semester"
    where = f" in {_course(out['course_code'])}" if out.get("course_code") else ""
    lines = [f"Result: {RESULT_WORDS[out['result']]} for the {kind} examination{where}."]
    for check in out["checks"]:
        mark = "met" if check["passed"] else "not met"
        if check["rule_id"]:
            lines.append(f"{_rule_line(check, cites)} Your record: {check['actual']} ({mark}).")
        else:
            lines.append(f"Your record shows you were {check['actual']} in this course ({mark}).")
    for a in out["assumptions"]:
        lines.append(f"Assumption: this {a}.")
    return " ".join(lines)


def placement_answer(out: dict, cites: dict[str, str]) -> str:
    prefix = "With your stated assumption, you would be" if out.get("assume_pass") else "You are"
    word = "eligible" if out["result"] == "ELIGIBLE" else "not eligible"
    lines = [f"Result: {prefix} {word} to register for campus placement."]
    for check in out["checks"]:
        mark = "met" if check["passed"] else "not met"
        lines.append(f"{_rule_line(check, cites)} Yours: {check['actual']} ({mark}).")
    for a in out["assumptions"]:
        lines.append(f"Assumption: {a}.")
    return " ".join(lines)


def results_answer(out: dict) -> str:
    rows = out["results"]
    parts = [f"{_course(r['course_code'])}: {r['result']} with {r['total_marks']}/{r['max_marks']} "
             f"({r['exam_type'].lower()}, {r['exam_session']})" for r in rows]
    return "Your results on record: " + "; ".join(parts) + "."
