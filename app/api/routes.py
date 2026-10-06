"""HTTP endpoints. Business logic lives in the graph, policy, tools and retrieval modules."""

import json
import logging
import re
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import database
from app.graph.workflow import run_question
from app.llm.client import ollama_status
from app.models.schemas import AskRequest, AskResponse, IngestResponse, LoginRequest, LoginResponse
from app.retrieval import store
from app.retrieval.ingest import ingest_document
from app.security import auth

log = logging.getLogger(__name__)
router = APIRouter()


def _today() -> str:
    return settings.default_as_of_date or date.today().isoformat()


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest):
    """Check student ID + password; return a signed token. One generic error for every failure."""
    student_id = auth.authenticate(body.student_id, body.password)
    attempted = body.student_id.strip().upper()
    if student_id is None:
        logged_id = attempted if re.fullmatch(r"S\d{4}", attempted) else "(malformed)"
        database.log_security_event("login_failure", logged_id, "bad credentials")
        raise HTTPException(status_code=401, detail=auth.GENERIC_LOGIN_ERROR, headers={"WWW-Authenticate": "Bearer"})
    token, lifetime = auth.create_access_token(student_id)
    database.log_security_event("login_success", student_id)
    return LoginResponse(access_token=token, expires_in=lifetime, student_id=student_id)


@router.post("/auth/logout", status_code=204)
def logout(claims: dict = Depends(auth.get_token_claims)):
    """Revoke this token: it stops working at once, even before it expires."""
    database.revoke_token(claims["jti"], int(claims["exp"]))
    database.log_security_event("logout", claims["sub"])


@router.get("/auth/me")
def me(student_id: str = Depends(auth.get_current_student)) -> dict:
    """The logged-in student's own profile, for the UI header. Never another student's, never the hash."""
    s = database.get_student(student_id)
    return {k: s[k] for k in ("student_id", "full_name", "programme", "batch_year", "current_semester")}


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, student_id: str = Depends(auth.get_current_student)):
    """Answer a question for the AUTHENTICATED student.

    Identity comes only from the verified Bearer token. Any X-Student-Id header, body field or ID written
    in the question is ignored as identity (an ID in the question only ever leads to a refusal).
    """
    as_of = body.as_of_date.isoformat() if body.as_of_date else _today()
    try:
        return run_question(body.question, as_of, student_id)
    except Exception:  # noqa: BLE001 - a failed dependency must give a clear error, never a made-up answer
        trace_id = str(uuid.uuid4())
        log.exception("ask failed (trace_id=%s)", trace_id)
        return JSONResponse(status_code=503, content={
            "trace_id": trace_id, "error": "The assistant is temporarily unavailable. Please try again.",
        })


@router.post("/ingest", response_model=IngestResponse)
async def ingest(file: Annotated[UploadFile, File()], metadata: Annotated[str, Form()]):
    """Upload a document (PDF/TXT/MD) plus its metadata JSON. It is searchable on the very next /ask."""
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        return JSONResponse(status_code=413, content=IngestResponse(
            doc_id="", chunks_indexed=0, status="rejected", errors=[f"file is larger than {settings.max_upload_mb} MB"]
        ).model_dump())
    try:
        meta = json.loads(metadata)
        if not isinstance(meta, dict):
            raise ValueError("metadata must be a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return JSONResponse(status_code=422, content=IngestResponse(
            doc_id="", chunks_indexed=0, status="rejected", errors=[f"invalid metadata JSON: {exc}"]
        ).model_dump())
    result = ingest_document(file.filename or "upload", data, meta)
    status_code = {"indexed": 200, "duplicate": 409, "rejected": 422}[result["status"]]
    return JSONResponse(status_code=status_code, content=IngestResponse(**result).model_dump())


@router.get("/health")
def health() -> dict:
    components = {
        "api": "ok",
        "vector_store": "ok" if store.ping() else "down",
        "sqlite": "ok" if database.ping() else "down",
        "llm": ollama_status(),
    }
    healthy = all(v in {"ok", "mock"} for v in components.values())
    return {
        "status": "ok" if healthy else "degraded",
        "components": components,
        "model": "mock" if settings.mock_llm else settings.ollama_model,
        "documents": len(database.all_documents()) if components["sqlite"] == "ok" else None,
        "chunks": store.count() if components["vector_store"] == "ok" else None,
    }


def _sources() -> dict:
    hidden = {"content_hash"}
    docs = [{k: v for k, v in d.items() if k not in hidden} for d in database.all_documents()]
    return {"count": len(docs), "documents": docs}


@router.get("/sources")
def sources() -> dict:
    """The Source Register: every ingested document and its metadata (guide name for this endpoint)."""
    return _sources()


@router.get("/metadata")
def metadata() -> dict:
    """Alias of /sources (the name used in our design brief)."""
    return _sources()


@router.get("/audit/{trace_id}")
def audit(trace_id: str, student_id: str = Depends(auth.get_current_student)) -> dict:
    """A student may read only the audit records of their own questions (others look like 'not found')."""
    record = database.get_audit(trace_id)
    if not record or record["student_id"] != student_id:
        raise HTTPException(status_code=404, detail="No audit record for this trace_id")
    for key in ("retrieved", "selected_documents", "precedence_decision", "tools", "applied_rules"):
        record[key] = json.loads(record[key])
    return record
