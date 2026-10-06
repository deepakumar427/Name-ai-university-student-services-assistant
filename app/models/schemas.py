"""Request and response shapes (the API contract from the hackathon guide, section 6.1)."""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings

AnswerType = Literal[
    "retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged"
]


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., min_length=1, max_length=settings.max_question_chars)
    as_of_date: date | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be empty")
        return value.strip()


class Citation(BaseModel):
    doc_id: str
    title: str
    section: str
    page: int | None
    version: str
    effective_from: str
    note: str = ""  # e.g. "upcoming change" or "conflicting source"


class ToolCall(BaseModel):
    tool: str
    input: dict[str, Any]
    output: dict[str, Any]
    status: str = "ok"


class AppliedRule(BaseModel):
    rule_id: str
    parameter: str
    operator: str
    value: str
    source_doc_id: str
    source_section: str


class Conflict(BaseModel):
    winner: str
    loser: str
    step: str
    reason: str


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation] = []
    tools_invoked: list[ToolCall] = []
    applied_rules: list[AppliedRule] = []
    conflicts_detected: list[Conflict] = []
    explanation: str = ""
    as_of_date: str


class IngestResponse(BaseModel):
    doc_id: str
    chunks_indexed: int
    status: Literal["indexed", "duplicate", "rejected"]
    errors: list[str] = []
    warnings: list[str] = []


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    student_id: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
    student_id: str
