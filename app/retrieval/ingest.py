"""Ingestion pipeline: validate metadata -> read -> chunk -> clean -> embed + store -> register.

All-or-nothing: if any step fails, nothing from this document stays in ChromaDB or SQLite.
"""

import hashlib
import logging
import re
from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.db import database
from app.retrieval import store
from app.retrieval.chunker import chunk_pages
from app.retrieval.loaders import DocumentReadError, read_pages
from app.security.injection import PLACEHOLDER, sanitize

log = logging.getLogger(__name__)

DOC_TYPES = {"regulation", "circular", "notice", "faq", "handbook", "unofficial"}
OPERATORS = {">=", ">", "<=", "<", "==", "in"}

# Short names from our brief -> field names used by the hackathon guide.
ALIASES = {
    "program": "scope_programmes",
    "programme": "scope_programmes",
    "batch": "scope_batches",
    "source": "provenance",
    "authority": "authority_level",
    "document_type": "doc_type",
    "effective_date": "effective_from",
    "expiry_date": "effective_to",
}


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    parameter: str
    operator: str
    value: str
    source_section: str
    description: str = ""
    scope_programmes: str | None = None
    scope_batches: str | None = None
    effective_from: date | None = None
    effective_to: date | None = None

    @field_validator("operator")
    @classmethod
    def known_operator(cls, v: str) -> str:
        if v not in OPERATORS:
            raise ValueError(f"operator must be one of {sorted(OPERATORS)}")
        return v


class DocumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_id: str = Field(pattern=r"^[A-Za-z0-9._-]{2,64}$")
    title: str = Field(min_length=2)
    issuer: str = Field(min_length=2)
    authority_level: int = Field(ge=1, le=5)
    doc_type: str
    version: str = Field(min_length=1)
    effective_from: date
    effective_to: date | None = None
    supersedes: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = Field(min_length=2)
    retrieved_on: str = ""
    synthetic: str = "N"
    rules: list[RuleIn] = []

    @field_validator("doc_type")
    @classmethod
    def known_type(cls, v: str) -> str:
        if v.lower() not in DOC_TYPES:
            raise ValueError(f"doc_type must be one of {sorted(DOC_TYPES)}")
        return v.lower()

    @field_validator("effective_to", mode="before")
    @classmethod
    def blank_is_none(cls, v):
        return None if v in ("", None) else v

    @field_validator("synthetic")
    @classmethod
    def yes_no(cls, v: str) -> str:
        if v.upper() not in {"Y", "N"}:
            raise ValueError("synthetic must be Y or N")
        return v.upper()

    @field_validator("supersedes")
    @classmethod
    def supersedes_format(cls, v: str) -> str:
        for entry in [e.strip() for e in v.split(";") if e.strip()]:
            if not re.match(r"^[A-Za-z0-9._-]+(#[A-Za-z0-9.]+)?$", entry):
                raise ValueError(f"supersedes entry '{entry}' must look like DOC-ID or DOC-ID#7.2")
        return ";".join(e.strip() for e in v.split(";") if e.strip())

    @model_validator(mode="after")
    def dates_in_order(self):
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        return self


def normalise_keys(raw: dict) -> dict:
    return {ALIASES.get(k, k): v for k, v in raw.items()}


def _errors(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'metadata'}: {e['msg']}" for e in exc.errors()]


def ingest_document(file_name: str, data: bytes, raw_metadata: dict) -> dict:
    """Return {doc_id, chunks_indexed, status, errors, warnings}."""
    doc_id = str(raw_metadata.get("doc_id", ""))
    try:
        meta = DocumentMetadata(**normalise_keys(raw_metadata))
    except ValidationError as exc:
        return _result(doc_id, 0, "rejected", _errors(exc))

    if database.get_document(meta.doc_id):
        return _result(meta.doc_id, 0, "duplicate", [f"doc_id '{meta.doc_id}' is already ingested"])
    content_hash = hashlib.sha256(data).hexdigest()
    same = database.find_document_by_hash(content_hash)
    if same:
        return _result(meta.doc_id, 0, "duplicate", [f"same file content already ingested as '{same['doc_id']}'"])

    try:
        pages = read_pages(file_name, data)
    except DocumentReadError as exc:
        return _result(meta.doc_id, 0, "rejected", [str(exc)])

    chunks = chunk_pages(pages, meta.doc_id, meta.title)
    if not chunks:
        return _result(meta.doc_id, 0, "rejected", ["document produced no chunks"])

    warnings: list[str] = []
    flagged = 0
    records = []
    for chunk in chunks:
        header, _, body = chunk["text"].partition("\n")
        clean_body, removed = sanitize(body)
        flagged += 1 if removed else 0
        if not clean_body.replace(PLACEHOLDER, "").strip():
            continue  # nothing but instruction-like text: not indexed at all
        clean_text = f"{header}\n{clean_body}"
        records.append(
            {"id": chunk["id"], "text": clean_text, "metadata": _chunk_metadata(meta, chunk, bool(removed))}
        )
    if flagged:
        warnings.append(f"{flagged} chunk(s) contained instruction-like text; it was removed and logged")
    warnings.extend(_check_supersedes(meta))

    try:
        store.add_chunks(records)
        database.insert_document(_register_row(meta, file_name, content_hash, len(records), flagged))
        for rule in meta.rules:
            database.upsert_rule(_rule_row(meta, rule))
    except Exception as exc:  # noqa: BLE001 - roll back and report, never leave half a document
        log.exception("Ingestion failed for %s", meta.doc_id)
        _rollback(meta.doc_id)
        return _result(meta.doc_id, 0, "rejected", [f"ingestion error: {exc}"])

    log.info("Ingested %s (%d chunks, %d flagged)", meta.doc_id, len(records), flagged)
    return _result(meta.doc_id, len(records), "indexed", [], warnings)


def _result(doc_id, chunks, status, errors, warnings=None) -> dict:
    return {"doc_id": doc_id, "chunks_indexed": chunks, "status": status, "errors": errors, "warnings": warnings or []}


def _chunk_metadata(meta: DocumentMetadata, chunk: dict, flagged: bool) -> dict:
    return {
        "doc_id": meta.doc_id,
        "title": meta.title,
        "issuer": meta.issuer,
        "authority_level": meta.authority_level,
        "doc_type": meta.doc_type,
        "version": meta.version,
        "effective_from": meta.effective_from.isoformat(),
        "effective_to": meta.effective_to.isoformat() if meta.effective_to else "",
        "supersedes": meta.supersedes,
        "scope_programmes": meta.scope_programmes,
        "scope_batches": meta.scope_batches,
        "synthetic": meta.synthetic,
        "section": chunk["section"],
        "page": chunk["page"],
        "flagged": flagged,
    }


def _register_row(meta: DocumentMetadata, file_name: str, content_hash: str, chunks: int, flagged: int) -> dict:
    row = meta.model_dump(exclude={"rules"})
    row["effective_from"] = meta.effective_from.isoformat()
    row["effective_to"] = meta.effective_to.isoformat() if meta.effective_to else ""
    row.update(
        file_name=file_name,
        content_hash=content_hash,
        chunks_indexed=chunks,
        flagged_chunks=flagged,
        ingested_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    return row


def _rule_row(meta: DocumentMetadata, rule: RuleIn) -> dict:
    effective_to = rule.effective_to or meta.effective_to
    return {
        "rule_id": rule.rule_id,
        "description": rule.description or f"{rule.parameter} from {meta.doc_id}",
        "parameter": rule.parameter,
        "operator": rule.operator,
        "value": rule.value,
        "scope_programmes": rule.scope_programmes or meta.scope_programmes,
        "scope_batches": rule.scope_batches or meta.scope_batches,
        "effective_from": (rule.effective_from or meta.effective_from).isoformat(),
        "effective_to": effective_to.isoformat() if effective_to else "",
        "source_doc_id": meta.doc_id,
        "source_section": rule.source_section,
    }


def _check_supersedes(meta: DocumentMetadata) -> list[str]:
    warnings = []
    for entry in [e for e in meta.supersedes.split(";") if e]:
        target = entry.split("#")[0]
        if not database.get_document(target):
            warnings.append(f"supersedes '{entry}': document '{target}' is not ingested (yet)")
    if meta.supersedes and meta.authority_level > 2:
        warnings.append("supersedes is ignored for authority levels 3-5 (only level 1 or 2 can supersede)")
    return warnings


def _rollback(doc_id: str) -> None:
    try:
        store.delete_document(doc_id)
    except Exception:  # noqa: BLE001
        log.exception("Rollback of Chroma chunks failed for %s", doc_id)
    with database.connect() as conn:
        conn.execute("DELETE FROM source_register WHERE doc_id = ?", (doc_id,))
        conn.execute("DELETE FROM rule_registry WHERE source_doc_id = ?", (doc_id,))
