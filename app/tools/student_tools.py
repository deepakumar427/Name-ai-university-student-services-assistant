"""Deterministic tools. CODE CALCULATES, AI EXPLAINS.

Rules for every tool:
- The student ID comes from ToolContext (the authenticated login), never from an argument.
- Thresholds come from rule_registry through the precedence engine, never from constants here.
- Nothing writes to the database. What-if checks work on in-memory copies.
"""

from dataclasses import dataclass, field
from fractions import Fraction

from app.db import database
from app.policy.precedence import Scope
from app.policy.resolver import PolicyOutcome, resolve_parameter


@dataclass
class ToolContext:
    student_id: str
    student: dict
    as_of: str
    hits: list[dict] = field(default_factory=list)  # retrieved chunks, so new documents can compete
    rule_cache: dict[str, PolicyOutcome] = field(default_factory=dict)

    @property
    def scope(self) -> Scope:
        return Scope(self.student["programme"], int(self.student["batch_year"]))


def compare(actual: float | Fraction | str, operator: str, value: str) -> bool:
    if operator == "in":
        return str(actual).upper() in {v.strip().upper() for v in value.split(";")}
    threshold = Fraction(value)
    actual = Fraction(actual) if not isinstance(actual, Fraction) else actual
    return {
        ">=": actual >= threshold,
        ">": actual > threshold,
        "<=": actual <= threshold,
        "<": actual < threshold,
        "==": actual == threshold,
    }[operator]


def pct(value: Fraction) -> float:
    return round(float(value), 2)


# ---------- 1. get_attendance ----------

def get_attendance(ctx: ToolContext, course_code: str) -> dict:
    row = database.get_attendance_row(ctx.student_id, course_code)
    if not row:
        return {"status": "no_record", "course_code": course_code}
    exact = Fraction(row["classes_attended"] * 100, row["classes_held"])
    return {
        "status": "ok",
        "course_code": course_code,
        "classes_held": row["classes_held"],
        "classes_attended": row["classes_attended"],
        "attendance_pct": pct(exact),
        "_exact": exact,  # full precision for comparisons; stripped before output
    }


# ---------- 2. get_results ----------

def get_results(ctx: ToolContext, course_code: str | None = None) -> dict:
    rows = database.get_result_rows(ctx.student_id, course_code)
    return {"status": "ok" if rows else "no_record", "course_code": course_code, "results": rows}


# ---------- 3. get_rule ----------

def get_rule(ctx: ToolContext, parameter: str) -> dict:
    """Governing rule for this student on as_of_date, chosen by the precedence engine."""
    if parameter not in ctx.rule_cache:
        ctx.rule_cache[parameter] = resolve_parameter(parameter, ctx.scope, ctx.as_of, ctx.hits)
    outcome = ctx.rule_cache[parameter]
    if outcome.status == "ok" and outcome.rule:
        r = outcome.rule
        return {"status": "ok", "parameter": parameter, "rule_id": r["rule_id"], "operator": r["operator"],
                "value": r["value"], "source_doc_id": r["source_doc_id"], "source_section": r["source_section"]}
    status = {"flagged": "conflict", "unstructured": "rule_not_structured"}.get(outcome.status, "rule_not_available")
    return {"status": status, "parameter": parameter, "governing_doc": outcome.governing_doc}


def required_text(rule: dict) -> str:
    """Human-readable requirement, e.g. '>= 80%' or 'one of FAIL, ABSENT'."""
    if rule["operator"] == "in":
        return "one of " + ", ".join(v.strip() for v in rule["value"].split(";"))
    unit = "%" if rule["parameter"].endswith("_pct") else ""
    return f"{rule['operator']} {rule['value']}{unit}"


def _rule_problem(rule: dict) -> dict:
    """The rule could not be used (missing, disputed, or only in text). Never fall back to a constant."""
    return {"status": rule["status"], "result": "UNKNOWN", "parameter": rule["parameter"],
            "governing_doc": rule.get("governing_doc", "")}


def _check(rule: dict, label: str, actual, shown: str) -> dict:
    passed = compare(actual, rule["operator"], rule["value"])
    return {"check": label, "rule_id": rule["rule_id"], "required": required_text(rule), "actual": shown,
            "passed": passed}


def _latest_regular(results: list[dict]) -> dict | None:
    regular = [r for r in results if r["exam_type"] == "REGULAR"]
    return sorted(regular, key=lambda r: r["exam_session"])[-1] if regular else None


# ---------- 4. check_exam_eligibility ----------

def check_exam_eligibility(ctx: ToolContext, course_code: str, exam_type: str = "REGULAR",
                           hypothetical_pct: float | None = None) -> dict:
    checks, rules, assumptions = [], [], []
    results = database.get_result_rows(ctx.student_id, course_code) if course_code else []

    if exam_type == "SUPPLEMENTARY":
        rule = get_rule(ctx, "supp_eligible_results")
        if rule["status"] != "ok":
            return _rule_problem(rule)
        rules.append(rule)
        latest = _latest_regular(results)
        if not latest:
            return {"status": "no_record", "result": "UNKNOWN", "course_code": course_code,
                    "reason": "no regular-exam result on record for this course"}
        checks.append(_check(rule, "regular exam result", latest["result"], latest["result"]))
    else:
        rule = get_rule(ctx, "min_attendance_pct")
        if rule["status"] != "ok":
            return _rule_problem(rule)
        rules.append(rule)
        if hypothetical_pct is not None:
            actual = Fraction(str(hypothetical_pct))
            shown = f"{hypothetical_pct:g}% (assumed, as stated in the question)"
            assumptions.append(f"uses the attendance you stated ({hypothetical_pct:g}%), not your record")
        else:
            att = get_attendance(ctx, course_code)
            if att["status"] != "ok":
                return {"status": "no_record", "result": "UNKNOWN", "course_code": course_code,
                        "reason": "no attendance record for this course"}
            actual = att["_exact"]
            shown = f"{att['attendance_pct']:g}% ({att['classes_attended']} of {att['classes_held']} classes)"
        checks.append(_check(rule, "attendance", actual, shown))
        if any(r["result"] == "DETAINED" for r in results):
            checks.append({"check": "not detained in this course", "rule_id": "", "required": "not DETAINED",
                           "actual": "DETAINED", "passed": False})

    eligible = all(c["passed"] for c in checks)
    return {"status": "ok", "course_code": course_code, "exam_type": exam_type,
            "result": "ELIGIBLE" if eligible else "NOT_ELIGIBLE", "checks": checks, "rules": rules,
            "assumptions": assumptions}


# ---------- 5. check_placement_eligibility ----------

def check_placement_eligibility(ctx: ToolContext, assume_pass: str | None = None) -> dict:
    cgpa_rule = get_rule(ctx, "min_cgpa_placement")
    backlog_rule = get_rule(ctx, "max_active_backlogs_placement")
    for rule in (cgpa_rule, backlog_rule):
        if rule["status"] != "ok":
            return _rule_problem(rule)

    cgpa = Fraction(str(ctx.student["cgpa"]))
    backlogs = int(ctx.student["active_backlogs"])
    assumptions = []
    if assume_pass:
        latest = _latest_regular(database.get_result_rows(ctx.student_id, assume_pass))
        if latest and latest["result"] in {"FAIL", "ABSENT"} and backlogs > 0:
            backlogs -= 1  # in memory only; the database is never changed
            assumptions.append(f"assumes you pass {assume_pass} in the supplementary examination "
                               f"(active backlogs {backlogs + 1} -> {backlogs})")
        else:
            assumptions.append(f"{assume_pass} is not an active backlog on your record, so passing it changes nothing")
        assumptions.append("CGPA is kept at its current value: recalculating it needs grade points, which the "
                           "records do not contain")

    checks = [
        _check(cgpa_rule, "CGPA", cgpa, f"{float(cgpa):.2f}"),
        _check(backlog_rule, "active backlogs", backlogs, str(backlogs)),
    ]
    eligible = all(c["passed"] for c in checks)
    return {"status": "ok", "result": "ELIGIBLE" if eligible else "NOT_ELIGIBLE", "checks": checks,
            "rules": [cgpa_rule, backlog_rule], "assumptions": assumptions, "assume_pass": assume_pass}


def public(output: dict) -> dict:
    """Tool output safe to return/audit: drop internal fields such as exact fractions."""
    return {k: v for k, v in output.items() if not k.startswith("_")}
