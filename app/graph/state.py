"""The shared state that flows through every LangGraph node."""

from typing import Any, TypedDict


class GraphState(TypedDict, total=False):
    # input
    trace_id: str
    question: str
    as_of: str
    auth_student_id: str | None  # ALWAYS from a verified login token, never from the client or the LLM

    # 1 understand
    question_type: str
    classified_by: str
    personal: bool
    course_code: str | None
    exam_type: str
    hypothetical_pct: float | None
    assume_pass: bool
    other_students: list[str]

    # 2 authorize
    student: dict | None
    parameters: list[str]

    # 3-5 retrieve, evidence, policy
    hits: list[dict]
    evidence: list[dict]  # labelled chunks allowed into the answer
    conflicts: list[dict]
    upcoming: list[dict]
    flagged_chunks: list[dict]
    governing_doc: str
    policy_log: list[str]
    tool_ctx: Any

    # 6-7 tools
    tool_calls: list[dict]
    applied_rules: list[dict]
    calc: dict  # what the calculated template needs

    # 8-9 answer + checks
    draft: str
    attempts: int
    citation_check: str

    # result
    answer_type: str
    answer: str
    citations: list[dict]
    explanation: str
    llm_calls: int
    model: str
