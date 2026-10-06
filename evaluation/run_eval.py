"""Run the evaluation set through the real API code and write a report.

Scoring is deterministic (no LLM judge):
- answer correctness : answer_type matches AND every expected phrase is in the answer AND no forbidden phrase is
- citation accuracy  : expected source documents are cited AND each cited section's text contains the expected value
- abstention accuracy: not-answerable questions return not_found; answerable ones do not
- authorization      : other-student / no-identity / injection-in-question cases are refused and leak nothing
- tool correctness   : the expected tool ran and returned the expected result
- retrieval hit rate : an expected source appears in the top-12 retrieved chunks (from the audit record)
- conflict accuracy  : the expected losing documents are listed in conflicts_detected
- latency p50 / p95 and LLM calls per question

Usage:
  MOCK_LLM=true  python evaluation/run_eval.py     # extractive answers, no Ollama needed
  MOCK_LLM=false python evaluation/run_eval.py     # answers written by the Ollama model
"""

import json
import logging
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402
from app.main import app  # noqa: E402
from app.retrieval import store  # noqa: E402

AS_OF = "2026-10-06"
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpx2").setLevel(logging.WARNING)


DEFAULT_STUDENT = "S1001"
_tokens: dict[str, dict] = {}


def token_headers(client, student_id: str) -> dict:
    """Log in with the demo password (set by scripts/load_students.py) and cache the token."""
    if student_id not in _tokens:
        resp = client.post("/auth/login", json={"student_id": student_id, "password": settings.demo_student_password})
        resp.raise_for_status()
        _tokens[student_id] = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    return _tokens[student_id]


def load_questions() -> list[dict]:
    return [json.loads(line) for line in (ROOT / "evaluation" / "questions.jsonl").read_text().splitlines() if line]


def cited_text(citation: dict) -> str:
    return " ".join(c["text"] for c in store.get_section_chunks(citation["doc_id"], citation["section"]))


def score(q: dict, r: dict, audit: dict) -> dict:
    answer = r["answer"]
    type_ok = r["answer_type"] == q["expected_type"]
    contains_ok = all(s.lower() in answer.lower() for s in q.get("expected_contains", []))
    absent_ok = all(s.lower() not in answer.lower() for s in q.get("expected_absent", []))
    result = {"id": q["id"], "category": q["category"], "answer_type": r["answer_type"],
              "correct": type_ok and contains_ok and absent_ok}

    expected_sources = set(q.get("expected_sources", []))
    if expected_sources:
        cited = {c["doc_id"] for c in r["citations"]}
        values = [v for v in q.get("expected_contains", []) if v not in {"Assumption"}]
        supported = all(any(v.lower() in cited_text(c).lower() for c in r["citations"]) for v in values) \
            if r["answer_type"] == "retrieved_fact" else True
        result["citation_ok"] = expected_sources <= cited and supported
        retrieved_docs = {c["chunk_id"].split("::")[0] for c in audit.get("retrieved", [])}
        result["retrieval_hit"] = bool(expected_sources & retrieved_docs)

    if q["expected_type"] == "not_found" or r["answer_type"] == "not_found":
        result["abstention_ok"] = (q["expected_type"] == "not_found") == (r["answer_type"] == "not_found")
    if q["category"] in {"other_student", "no_identity", "injection_question"}:  # no_identity = HTTP 401
        result["authorization_ok"] = r["answer_type"] == "refused" and not r["tools_invoked"] and absent_ok
    if "expected_tool" in q:
        tools = {t["tool"]: t["output"] for t in r["tools_invoked"]}
        ok = q["expected_tool"] in tools
        if ok and "expected_result" in q:
            ok = tools[q["expected_tool"]].get("result") == q["expected_result"]
        result["tool_ok"] = ok
    if "expected_losers" in q:
        losers = {c["loser"] for c in r["conflicts_detected"]}
        result["conflict_ok"] = set(q["expected_losers"]) <= losers
    if q["expected_type"] == "conflict_flagged":
        result["conflict_ok"] = r["answer_type"] == "conflict_flagged"
    result["latency_ms"] = audit.get("latency_ms", 0)
    result["llm_calls"] = audit.get("llm_calls", 0)
    result["answer"] = answer[:220]
    return result


def rate(rows: list[dict], key: str) -> str:
    values = [r[key] for r in rows if key in r]
    return f"{sum(values)}/{len(values)} ({100 * sum(values) / len(values):.0f}%)" if values else "n/a"


def percentile(values: list[int], p: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))] if ordered else 0


def main() -> int:
    database.init_db()
    config = "mock (extractive answers)" if settings.mock_llm else f"ollama {settings.ollama_model}"
    rows = []
    with TestClient(app) as client:
        for q in load_questions():
            # Every question is asked by a logged-in student, exactly like the UI (general ones as S1001).
            # "no_auth" questions are sent with no token at all: the API must answer 401.
            headers = {} if q.get("no_auth") else token_headers(client, q.get("student_id", DEFAULT_STUDENT))
            started = time.perf_counter()
            resp = client.post("/ask", json={"question": q["question"], "as_of_date": q.get("as_of", AS_OF)},
                               headers=headers)
            if resp.status_code == 401:
                r = {"trace_id": "", "answer": "HTTP 401: not authenticated", "answer_type": "refused",
                     "citations": [], "tools_invoked": [], "conflicts_detected": []}
                audit = {}
            else:
                r = resp.json()
                audit = client.get(f"/audit/{r['trace_id']}", headers=headers).json()
            row = score(q, r, audit)
            row["wall_ms"] = int((time.perf_counter() - started) * 1000)
            rows.append(row)
            print(f"{row['id']} {'PASS' if row['correct'] else 'FAIL'} {row['answer_type']:<20} {row['answer'][:90]}")

    latencies = [r["latency_ms"] for r in rows]
    metrics = {
        "configuration": config,
        "questions": len(rows),
        "answer_correctness": rate(rows, "correct"),
        "citation_accuracy": rate(rows, "citation_ok"),
        "abstention_accuracy": rate(rows, "abstention_ok"),
        "authorization_correctness": rate(rows, "authorization_ok"),
        "tool_correctness": rate(rows, "tool_ok"),
        "retrieval_hit_rate": rate(rows, "retrieval_hit"),
        "conflict_accuracy": rate(rows, "conflict_ok"),
        "held_out_correctness": rate([r for r, q in zip(rows, load_questions()) if q.get("held_out")], "correct"),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "avg_llm_calls": round(statistics.mean(r["llm_calls"] for r in rows), 2),
    }
    tag = "mock" if settings.mock_llm else "ollama"
    out_dir = ROOT / "evaluation" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"results_{tag}.json").write_text(json.dumps({"metrics": metrics, "rows": rows}, indent=2))
    lines = [f"# Evaluation report — {config}", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in metrics.items()]
    lines += ["", "| ID | Category | Type | Correct | Answer |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {r['category']} | {r['answer_type']} | {'yes' if r['correct'] else 'NO'} | "
              f"{r['answer'][:110].replace('|', '/')} |" for r in rows]
    (out_dir / f"report_{tag}.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
