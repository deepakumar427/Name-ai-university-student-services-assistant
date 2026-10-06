"""LangGraph nodes. Each node is a small function: state in -> partial state update out.

Only two nodes use the LLM: `understand` (classify the question) and `generate_answer`
(write a policy answer from evidence). Authorization, precedence, tools, answer type and
citations are decided by plain Python.
"""

import json
import logging
import re

from langchain_core.runnables import RunnableConfig

from app.config import settings
from app.db import database
from app.llm.client import LLMError, OllamaClient
from app.llm.prompts import ANSWER_SYSTEM, answer_prompt
from app.policy.precedence import Scope
from app.policy.resolver import resolve_general, resolve_parameter
from app.policy.topics import PARAMETERS, detect_parameter
from app.retrieval import store
from app.security.citations import LABEL, check_answer, leaks_other_student, numbers_in
from app.services import answers, calculated
from app.services.understanding import understand as understand_question
from app.tools import student_tools as tools

log = logging.getLogger(__name__)
STUDENT_ID_FORMAT = re.compile(r"^S\d{4}$")


def _client(config: RunnableConfig | None) -> OllamaClient | None:
    """The per-request LLM client travels in the LangGraph config (None in mock mode)."""
    if settings.mock_llm or not config:
        return None
    return config.get("configurable", {}).get("llm")


def _label(chunks: list[dict]) -> list[dict]:
    return [{**c, "label": f"E{i}"} for i, c in enumerate(chunks, start=1)]


def _scope(state) -> Scope:
    s = state.get("student")
    return Scope(s["programme"], int(s["batch_year"])) if s else Scope()


# ---------- 1. understand question ----------

def understand(state: dict, config: RunnableConfig | None = None) -> dict:
    return understand_question(state["question"], state.get("auth_student_id"), _client(config))


# ---------- 2. authorization and clarity ----------

def authorize(state: dict) -> dict:
    # Authentication already happened at the API (verified JWT). This node is AUTHORIZATION:
    # what may this authenticated student see? It still refuses if no identity reaches it.
    auth_id = (state.get("auth_student_id") or "").strip().upper() or None
    student = database.get_student(auth_id) if auth_id and STUDENT_ID_FORMAT.match(auth_id) else None

    if state["other_students"]:
        return {"answer_type": "refused", "answer": answers.REFUSED,
                "explanation": "The question asks about another student's records."}
    if state["personal"] and not student:
        return {"answer_type": "refused", "answer": answers.REFUSED,
                "explanation": "Personal questions need an authenticated student identity."}

    profile = None
    if student:  # only the fields the workflow needs; the name is never passed on
        profile = {k: student[k] for k in ("student_id", "programme", "batch_year", "current_semester",
                                            "cgpa", "active_backlogs")}

    qtype, course = state["question_type"], state.get("course_code")
    needs_course = qtype == "attendance" or (qtype == "exam_eligibility" and state.get("hypothetical_pct") is None)
    if needs_course and not course:
        options = database.student_courses(profile["student_id"])
        if len(options) == 1:
            course = options[0]["course_code"]
        else:
            what = "your attendance" if qtype == "attendance" else "exam eligibility"
            return {"student": profile, "answer_type": "clarification_needed",
                    "answer": answers.clarification(options, what),
                    "explanation": "The question does not say which course."}
    if qtype == "placement_eligibility" and state.get("assume_pass") and not course:
        return {"student": profile, "answer_type": "clarification_needed",
                "answer": "Which course do you expect to pass? Please name the course code, for example CS301.",
                "explanation": "The what-if question does not say which course."}

    return {"student": profile, "course_code": course, "parameters": _parameters(state)}


def _parameters(state: dict) -> list[str]:
    qtype = state["question_type"]
    if qtype in ("attendance",):
        return ["min_attendance_pct"]
    if qtype == "exam_eligibility":
        return ["supp_eligible_results"] if state["exam_type"] == "SUPPLEMENTARY" else ["min_attendance_pct"]
    if qtype == "placement_eligibility":
        return ["min_cgpa_placement", "max_active_backlogs_placement"]
    if qtype == "policy":
        found = detect_parameter(state["question"])
        return [found] if found else []
    return []


# ---------- 3. search documents ----------

def retrieve(state: dict) -> dict:
    queries = [state["question"]] + [PARAMETERS[p]["query"] for p in state.get("parameters", [])]
    merged: dict[str, dict] = {}
    for i, query in enumerate(queries):
        for hit in store.search(query):
            # q_score = similarity to the student's own question; used to order the evidence.
            hit["q_score"] = hit["score"] if i == 0 else 0.0
            old = merged.get(hit["chunk_id"])
            if old is None or hit["score"] > old["score"]:
                merged[hit["chunk_id"]] = {**hit, "q_score": max(hit["q_score"], old["q_score"] if old else 0.0)}
    hits = sorted(merged.values(), key=lambda h: h["score"], reverse=True)
    return {"hits": hits}


# ---------- 4. enough evidence? ----------

def check_evidence(state: dict) -> dict:
    if state["question_type"] != "policy":
        return {}  # tool questions are grounded in records + the cited rule clause
    relevant = [h for h in state["hits"] if h["score"] >= settings.similarity_threshold]
    if not relevant:
        return {"answer_type": "not_found", "answer": answers.NOT_FOUND,
                "explanation": "No document passage reached the similarity threshold "
                               f"({settings.similarity_threshold})."}
    return {}


# ---------- 5. resolve the correct document / rule ----------

def resolve_policy(state: dict) -> dict:
    scope, as_of, hits = _scope(state), state["as_of"], state["hits"]
    if state["question_type"] != "policy":
        ctx = tools.ToolContext(state["student"]["student_id"], state["student"], as_of, hits)
        conflicts, upcoming, log_lines = [], [], []
        for p in state["parameters"]:
            outcome = resolve_parameter(p, scope, as_of, hits)
            ctx.rule_cache[p] = outcome
            conflicts += outcome.conflicts
            upcoming += outcome.upcoming
            log_lines += outcome.log
            if outcome.status == "flagged":
                return _flagged(state, outcome.flagged_chunks, conflicts, log_lines)
        return {"tool_ctx": ctx, "conflicts": conflicts, "upcoming": upcoming, "policy_log": log_lines}

    outcome = None
    if state["parameters"]:
        outcome = resolve_parameter(state["parameters"][0], scope, as_of, hits)
        if outcome.status == "not_available":
            outcome = None  # no rule and no comparable text: treat it as an ordinary document question
    outcome = outcome or resolve_general(hits, scope, as_of)
    if outcome.status == "flagged":
        return _flagged(state, outcome.flagged_chunks, outcome.conflicts, outcome.log)
    if not outcome.evidence:
        return {"answer_type": "not_found", "answer": answers.NOT_FOUND, "conflicts": outcome.conflicts,
                "explanation": "Relevant passages exist, but none is in force or applicable on this date."}
    return {"evidence": _label(outcome.evidence), "conflicts": outcome.conflicts, "upcoming": outcome.upcoming,
            "governing_doc": outcome.governing_doc, "policy_log": outcome.log}


def _flagged(state, chunks, conflicts, log_lines) -> dict:
    labelled = _label(chunks)
    return {"answer_type": "conflict_flagged", "flagged_chunks": labelled,
            "answer": answers.conflict_answer(labelled, state["question"]), "conflicts": conflicts,
            "policy_log": log_lines,
            "explanation": "Sources of the same authority and the same effective date disagree (precedence step 5)."}


# ---------- 6-7. run deterministic tools ----------

def run_tools(state: dict) -> dict:
    ctx: tools.ToolContext = state["tool_ctx"]
    qtype, course = state["question_type"], state.get("course_code")
    calls: list[dict] = []

    def call(name, fn, **kwargs):
        out = fn(ctx, **kwargs)
        calls.append({"tool": name, "input": kwargs, "output": tools.public(out), "status": out["status"]})
        return out

    if qtype == "attendance":
        out = call("get_attendance", tools.get_attendance, course_code=course)
        rule = call("get_rule", tools.get_rule, parameter="min_attendance_pct")
        if out["status"] == "ok" and rule["status"] == "ok":
            rule["meets"] = tools.compare(out["_exact"], rule["operator"], rule["value"])
    elif qtype == "results":
        out = call("get_results", tools.get_results, course_code=course)
    elif qtype == "exam_eligibility":
        out = call("check_exam_eligibility", tools.check_exam_eligibility, course_code=course,
                   exam_type=state["exam_type"], hypothetical_pct=state.get("hypothetical_pct"))
    else:
        out = call("check_placement_eligibility", tools.check_placement_eligibility,
                   assume_pass=course if state.get("assume_pass") else None)

    if out["status"] != "ok":
        return {"tool_calls": calls, **_tool_problem(out, course, ctx)}

    # Evidence = the clause each applied rule came from, so calculated answers are cited.
    rules = [r for r in out.get("rules", []) if r.get("status") == "ok"]
    if qtype == "attendance" and rule["status"] == "ok":
        rules = [rule]
    evidence, cites = [], {}
    for r in rules:
        chunks = ctx.rule_cache[r["parameter"]].rule_chunks[:1]
        if chunks and r["rule_id"] not in cites:
            evidence.append(chunks[0])
            cites[r["rule_id"]] = f" [E{len(evidence)}]"
    applied = [{k: r[k] for k in ("rule_id", "parameter", "operator", "value", "source_doc_id", "source_section")}
               for r in rules]
    calc = {"qtype": qtype, "out": out, "rule": rule if qtype == "attendance" else None, "cites": cites}
    return {"tool_calls": calls, "applied_rules": applied, "evidence": _label(evidence), "calc": calc,
            "governing_doc": evidence[0]["metadata"]["doc_id"] if evidence else ""}


def _tool_problem(out: dict, course: str | None, ctx) -> dict:
    status = out["status"]
    if status == "no_record":
        return {"answer_type": "not_found",
                "answer": f"I could not find a matching record for {course or 'this request'} "
                          "in your university records.",
                "explanation": out.get("reason", "The records have no entry for this course.")}
    if status == "conflict":
        outcome = ctx.rule_cache.get(out.get("parameter", ""))
        chunks = _label(outcome.flagged_chunks if outcome else [])
        return {"answer_type": "conflict_flagged", "flagged_chunks": chunks,
                "answer": answers.conflict_answer(chunks, "minimum required") if chunks else answers.NOT_FOUND,
                "explanation": "The governing rule is disputed by sources of equal authority and date."}
    governing = out.get("governing_doc", "")
    note = f" The governing document appears to be {governing}, but it has no structured rule to calculate with." \
        if governing else ""
    return {"answer_type": "not_found", "answer": answers.NOT_FOUND,
            "explanation": f"The rule '{out.get('parameter')}' needed for this calculation is not available.{note}"}


# ---------- 8. generate the answer ----------

def generate_answer(state: dict, config: RunnableConfig | None = None) -> dict:
    if state.get("calc"):
        c = state["calc"]
        out, cites = c["out"], c["cites"]
        if c["qtype"] == "attendance":
            draft = calculated.attendance_answer(out, c["rule"], next(iter(cites.values()), ""))
        elif c["qtype"] == "results":
            draft = calculated.results_answer(out)
        elif c["qtype"] == "exam_eligibility":
            draft = calculated.exam_answer(out, cites)
        else:
            draft = calculated.placement_answer(out, cites)
        return {"draft": draft}

    attempts = state.get("attempts", 0)
    evidence = state["evidence"]
    client = _client(config)
    if client is None:
        return {"draft": answers.extractive_answer(evidence, state["question"]), "attempts": attempts + 1,
                "citation_check": "extractive (mock LLM)"}
    try:
        draft = client.generate(ANSWER_SYSTEM, answer_prompt(state["question"], evidence, strict=attempts > 0))
    except LLMError as exc:
        log.warning("LLM answer failed, using extractive fallback: %s", exc)
        return {"draft": answers.extractive_answer(evidence, state["question"]), "attempts": 99,
                "citation_check": "extractive fallback (LLM unavailable)"}
    return {"draft": draft, "attempts": attempts + 1}


# ---------- 9. validate citations ----------

def validate_citations(state: dict) -> dict:
    draft = state["draft"].strip()
    support_by_label = {e["label"]: e["text"] for e in state["evidence"]}

    if leaks_other_student(draft, state.get("auth_student_id")):
        return {"answer_type": "refused", "answer": answers.REFUSED, "citation_check": "blocked: other student ID"}

    if state.get("calc"):
        tool_text = json.dumps([c["output"] for c in state["tool_calls"]]) + " " + " ".join(support_by_label.values())
        missing = numbers_in(LABEL.sub("", draft)) - numbers_in(tool_text)
        if missing:  # cannot happen with templates, but never ship an unverified number
            return {"answer_type": "not_found", "answer": answers.NOT_FOUND,
                    "citation_check": f"failed: {sorted(missing)} not in tool output"}
        return {"answer_type": "calculated", "answer": draft, "citation_check": "ok (numbers match tool output)"}

    if draft.upper().startswith("NOT_FOUND"):
        if state.get("parameters"):
            # The precedence engine already found the governing rule clause; quoting it cannot invent anything.
            quoted = answers.extractive_answer(state["evidence"], state["question"])
            return {"answer_type": "retrieved_fact", "answer": quoted,
                    "citation_check": "model abstained; quoted the governing clause"}
        return {"answer_type": "not_found", "answer": answers.NOT_FOUND, "citation_check": "model found no answer"}

    ok, reason, _ = check_answer(draft, support_by_label)
    if ok:
        previous = state.get("citation_check", "")
        status = "ok after retry" if previous.startswith("retry") else (previous or "ok")
        return {"answer_type": "retrieved_fact", "answer": draft, "citation_check": status}
    if state.get("attempts", 0) < 2:
        return {"citation_check": f"retry: {reason}"}  # the router sends us back to generate_answer once

    fallback = answers.extractive_answer(state["evidence"], state["question"])
    ok, reason2, _ = check_answer(fallback, support_by_label)
    if ok:
        return {"answer_type": "retrieved_fact", "answer": fallback,
                "citation_check": f"fallback to quoted clause ({reason})"}
    return {"answer_type": "not_found", "answer": answers.NOT_FOUND, "citation_check": f"failed: {reason2}"}


# ---------- 10. finalize ----------

def finalize(state: dict) -> dict:
    answer_type = state["answer_type"]
    answer = state["answer"]
    citations, label_index = [], {}

    if answer_type in ("retrieved_fact", "calculated"):
        used = list(dict.fromkeys(LABEL.findall(answer)))
        by_label = {e["label"]: e for e in state.get("evidence", [])}
        for lab in used or [e["label"] for e in state.get("evidence", [])[:1]]:
            if lab in by_label:
                citations.append(answers.citation(by_label[lab]))
                label_index[lab] = len(citations)
        for chunk in state.get("upcoming", [])[:1]:
            citations.append(answers.citation(chunk, note="upcoming change, not yet in force"))
    elif answer_type == "conflict_flagged":
        citations = [answers.citation(c, note="conflicting source") for c in state.get("flagged_chunks", [])]

    answer = answers.renumber(answer, label_index)
    explanation = state.get("explanation", "")
    if answer_type in ("retrieved_fact", "calculated"):
        evidence = state.get("evidence", [])
        governing = next((e for e in evidence if e["metadata"]["doc_id"] == state.get("governing_doc")), None)
        explanation = answers.explain_policy(governing, state.get("conflicts", []), state.get("upcoming", [])[:1])
    return {"answer": answer, "citations": citations, "explanation": explanation}
