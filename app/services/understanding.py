"""Turn a question into structured facts. The LLM classifies; regex extracts IDs, courses and numbers.

Anything that affects security or numbers (student IDs, course codes, hypothetical percentages)
is extracted by plain code, never taken from the LLM.
"""

import logging
import re

from app.config import settings
from app.db import database
from app.llm.client import LLMError, OllamaClient
from app.llm.prompts import CLASSIFY_SYSTEM, classify_prompt

log = logging.getLogger(__name__)

QUESTION_TYPES = ["policy", "attendance", "results", "exam_eligibility", "placement_eligibility"]
TOOL_TYPES = set(QUESTION_TYPES) - {"policy"}

PERSONAL = re.compile(r"\b(my|me|mine|am i|i am|i'm|can i|do i|did i|will i|have i|should i|i have|i failed|"
                      r"i pass|if i)\b", re.IGNORECASE)
STUDENT_ID = re.compile(r"\bS\d{4}\b", re.IGNORECASE)
COURSE_CODE = re.compile(r"\b([A-Z]{2,4}\s?\d{3})\b", re.IGNORECASE)
HYPOTHETICAL_PCT = re.compile(r"\b(?:if|suppose|assuming)\b[^?.]*?(\d{1,3}(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
ASSUME_PASS = re.compile(r"\bif i (?:pass|clear)\b", re.IGNORECASE)
CONDITIONAL = re.compile(r"\b(if|suppose|assuming)\s+(i|my)\b", re.IGNORECASE)


def find_courses(question: str) -> list[str]:
    """Course codes mentioned by code ('CS201') or by name ('Data Structures'), validated against the DB."""
    found = []
    known = {c["course_code"]: c["course_name"] for c in database.all_courses()}
    for raw in COURSE_CODE.findall(question):
        code = raw.replace(" ", "").upper()
        if code in known and code not in found:
            found.append(code)
    q = question.lower()
    for code, name in known.items():
        if name.lower() in q and code not in found:
            found.append(code)
    return found


def find_other_students(question: str, auth_id: str | None) -> list[str]:
    """Other students referred to by ID or by full name (identity from the question is never trusted)."""
    others = [s.upper() for s in STUDENT_ID.findall(question) if s.upper() != (auth_id or "").upper()]
    q = question.lower()
    for row in database.fetch_all("SELECT student_id, full_name FROM students"):
        if row["student_id"] != auth_id and row["full_name"].lower() in q:
            others.append(row["student_id"])
    return sorted(set(others))


def keyword_classify(question: str) -> str:
    """Simple, predictable fallback used when the LLM is off, unavailable or returns nonsense."""
    q = question.lower()
    personal = bool(PERSONAL.search(question))
    if not personal:
        return "policy"
    if "placement" in q or "placed" in q:
        return "placement_eligibility"
    if any(w in q for w in ["eligible", "eligibility", "sit for", "appear", "allowed to", "can i take", "detained"]):
        return "exam_eligibility"
    if "attendance" in q:
        return "attendance"
    if any(w in q for w in ["result", "marks", "grade", "score", "did i pass", "failed", "backlog"]):
        return "results"
    return "policy"


def llm_classify(question: str, client: OllamaClient) -> str | None:
    try:
        data = client.generate_json(CLASSIFY_SYSTEM, classify_prompt(question))
    except LLMError as exc:
        log.warning("LLM classification failed, using keyword fallback: %s", exc)
        return None
    qtype = str(data.get("question_type", "")).strip().lower()
    return qtype if qtype in QUESTION_TYPES else None


def understand(question: str, auth_id: str | None, client: OllamaClient | None) -> dict:
    personal = bool(PERSONAL.search(question))
    qtype, source = None, "keywords"
    if client is not None and settings.llm_classify and not settings.mock_llm:
        qtype = llm_classify(question, client)
        source = "llm" if qtype else "keywords (llm fallback)"
    if qtype is None:
        qtype = keyword_classify(question)
    # Guard: a tool path needs a personal question. "What is the minimum attendance?" is a policy question.
    if qtype in TOOL_TYPES and not personal and not HYPOTHETICAL_PCT.search(question):
        qtype = "policy"
    # "Can I register for placement if I have a backlog?" with nobody signed in is a question about the rule.
    if qtype in TOOL_TYPES and not auth_id and CONDITIONAL.search(question):
        qtype = "policy"

    courses = find_courses(question)
    pct = HYPOTHETICAL_PCT.search(question)
    q = question.lower()
    return {
        "question_type": qtype,
        "classified_by": source,
        "personal": qtype in TOOL_TYPES,
        "courses": courses,
        "course_code": courses[0] if courses else None,
        "exam_type": "SUPPLEMENTARY" if "supplementary" in q or "supple" in q else "REGULAR",
        "hypothetical_pct": float(pct.group(1)) if pct else None,
        "assume_pass": bool(ASSUME_PASS.search(question)),
        "other_students": find_other_students(question, auth_id),
    }
