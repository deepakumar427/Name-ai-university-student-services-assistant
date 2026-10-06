"""Ingestion: chunking, metadata validation, duplicates, injection cleaning and live ingestion."""

import json

from conftest import ROOT, ask

from app.retrieval import store
from app.retrieval.chunker import chunk_pages

META = {"doc_id": "ACAD-2026-10", "title": "Circular: Attendance 85 percent", "issuer": "Office of the Dean (Academics)",
        "authority_level": 2, "doc_type": "circular", "version": "1.0", "effective_from": "2026-09-20",
        "supersedes": "ACAD-2026-08#2.1", "program": "ALL", "batch": "ALL", "source": "test"}
TEXT = (b"CIRCULAR No. ACAD-2026-10.\n\n1. Attendance\n1.1 With effect from 20 September 2026, a student must have a "
        b"minimum of 85% attendance in each course to appear in the end-semester examination.\n")


def upload(client, data: bytes, meta: dict, name="circular.txt"):
    return client.post("/ingest", files={"file": (name, data)}, data={"metadata": json.dumps(meta)})


def test_chunks_keep_section_and_page():
    pages = [(3, "7. Attendance\n7.2 Minimum attendance. A student must have 75% attendance in each course.\n"
                 "7.3 Condonation. The Dean may condone a shortage of up to 5% on medical grounds.")]
    chunks = chunk_pages(pages, "DOC", "Regs")
    assert [(c["section"], c["page"]) for c in chunks] == [("7.2", 3), ("7.3", 3)]
    assert chunks[0]["text"].startswith("[Regs | Section 7.2 | 7. Attendance]")


def test_sample_pdf_citation_page_is_real():
    chunks = store.get_section_chunks("ACAD-REG-2024", "7.2")
    assert chunks and chunks[0]["metadata"]["page"] == 3 and "75%" in chunks[0]["text"]
    from pypdf import PdfReader

    assert "75%" in PdfReader(ROOT / "data" / "documents" / "ACAD-REG-2024.pdf").pages[2].extract_text()


def test_injection_text_removed_at_ingestion():
    chunks = store.get_collection().get(where={"doc_id": "HELPDESK-NOTES"})
    joined = " ".join(chunks["documents"])
    assert "Ignore all previous instructions" not in joined
    assert "8 AM to 10 PM" in joined  # the legitimate content is kept
    from app.db import database

    assert database.get_document("HELPDESK-NOTES")["flagged_chunks"] == 1  # recorded for the audit trail


def test_missing_and_invalid_metadata_rejected(client):
    r = upload(client, TEXT, {"doc_id": "X1", "title": "x"})
    assert r.status_code == 422 and r.json()["status"] == "rejected"
    bad = {**META, "doc_id": "BAD-2", "authority_level": 9, "effective_from": "2026-02-30"}
    errors = " ".join(upload(client, TEXT, bad).json()["errors"])
    assert "authority_level" in errors and "effective_from" in errors
    assert client.post("/ingest", files={"file": ("a.txt", TEXT)}, data={"metadata": "{oops"}).status_code == 422


def test_unreadable_and_unsupported_files_rejected(client):
    assert upload(client, b"not a pdf", {**META, "doc_id": "BAD-3"}, "x.pdf").json()["status"] == "rejected"
    assert upload(client, b"data", {**META, "doc_id": "BAD-4"}, "x.exe").json()["status"] == "rejected"
    assert upload(client, b"   ", {**META, "doc_id": "BAD-5"}).json()["status"] == "rejected"


def test_live_ingestion_changes_answers_without_restart(client):
    r = upload(client, TEXT, META)
    assert r.status_code == 200 and r.json()["status"] == "indexed" and r.json()["chunks_indexed"] >= 1

    policy = ask(client, "What is the minimum attendance required for exams?")
    assert "85%" in policy["answer"] and policy["citations"][0]["doc_id"] == "ACAD-2026-10"
    assert {"loser": "ACAD-2026-08#2.1", "step": "supersession"}.items() <= {
        k: v for c in policy["conflicts_detected"] if c["loser"] == "ACAD-2026-08#2.1" for k, v in c.items()}.items()

    # The new circular has no structured rule, so a calculation must not silently use the old 80%.
    calc = ask(client, "Am I eligible for the CS201 exam?", "S1001")
    assert calc["answer_type"] == "not_found"
    assert "ACAD-2026-10" in calc["explanation"]

    # Duplicates are refused (same doc_id, or same content under another id).
    assert upload(client, TEXT, META).status_code == 409
    assert upload(client, TEXT, {**META, "doc_id": "COPY-1"}).json()["status"] == "duplicate"
    assert client.get("/sources").json()["count"] == 8


def test_live_ingestion_with_rule_updates_calculation(client):
    text = (b"CIRCULAR No. ACAD-2026-11.\n\n1. Attendance\n1.1 With effect from 1 October 2026, a student must have"
            b" a minimum of 70% attendance in each course to appear in the end-semester examination.\n")
    meta = {**META, "doc_id": "ACAD-2026-11", "effective_from": "2026-10-01", "supersedes": "ACAD-2026-10",
            "rules": [{"rule_id": "ATT-MIN-03", "parameter": "min_attendance_pct", "operator": ">=", "value": "70",
                       "source_section": "1.1"}]}
    assert upload(client, text, meta).json()["status"] == "indexed"
    r = ask(client, "Am I eligible for the CS201 exam?", "S1002")  # 77.5%
    assert r["answer"].startswith("Result: Eligible")
    assert r["applied_rules"][0]["rule_id"] == "ATT-MIN-03"
