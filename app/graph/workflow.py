"""The LangGraph workflow: one fixed flowchart with early exits.

START -> understand -> authorize -> retrieve -> check_evidence -> resolve_policy
      -> [run_tools] -> generate_answer -> validate_citations -> finalize -> END
Any node can end the question early (refused, clarification_needed, not_found, conflict_flagged);
every path still goes through `finalize`, so every answer gets citations checked and an audit record.
"""

import json
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from app.config import settings
from app.db import database
from app.graph import nodes
from app.graph.state import GraphState
from app.llm.client import OllamaClient


def _done_or(next_node: str):
    """Route to `finalize` if a node already decided the answer type, else to `next_node`."""
    def route(state: GraphState) -> str:
        return "finalize" if state.get("answer_type") else next_node
    return route


def _after_policy(state: GraphState) -> str:
    if state.get("answer_type"):
        return "finalize"
    return "run_tools" if state["question_type"] != "policy" else "generate_answer"


def _after_validation(state: GraphState) -> str:
    return "finalize" if state.get("answer_type") else "generate_answer"  # one retry, then fallback


@lru_cache(maxsize=1)
def build_graph():
    g = StateGraph(GraphState)
    for name in ["understand", "authorize", "retrieve", "check_evidence", "resolve_policy", "run_tools",
                 "generate_answer", "validate_citations", "finalize"]:
        g.add_node(name, getattr(nodes, name))
    g.add_edge(START, "understand")
    g.add_edge("understand", "authorize")
    g.add_conditional_edges("authorize", _done_or("retrieve"), ["retrieve", "finalize"])
    g.add_edge("retrieve", "check_evidence")
    g.add_conditional_edges("check_evidence", _done_or("resolve_policy"), ["resolve_policy", "finalize"])
    g.add_conditional_edges("resolve_policy", _after_policy, ["run_tools", "generate_answer", "finalize"])
    g.add_conditional_edges("run_tools", _done_or("generate_answer"), ["generate_answer", "finalize"])
    g.add_edge("generate_answer", "validate_citations")
    g.add_conditional_edges("validate_citations", _after_validation, ["generate_answer", "finalize"])
    g.add_edge("finalize", END)
    return g.compile()


def run_question(question: str, as_of: str, student_id: str | None) -> dict:
    """Run one question through the graph, write the audit record, return the API response dict.

    `student_id` must be the AUTHENTICATED identity (from a verified token), never a client-supplied value.
    """
    start = time.perf_counter()
    trace_id = str(uuid.uuid4())
    client = None if settings.mock_llm else OllamaClient()
    state = build_graph().invoke(
        {"trace_id": trace_id, "question": question, "as_of": as_of, "auth_student_id": student_id},
        config={"configurable": {"llm": client}},
    )
    latency_ms = int((time.perf_counter() - start) * 1000)
    response = {
        "trace_id": trace_id,
        "answer": state["answer"],
        "answer_type": state["answer_type"],
        "citations": state.get("citations", []),
        "tools_invoked": state.get("tool_calls", []),
        "applied_rules": state.get("applied_rules", []),
        "conflicts_detected": state.get("conflicts", []),
        "explanation": state.get("explanation", ""),
        "as_of_date": as_of,
    }
    _audit(state, response, client, latency_ms)
    return response


def _audit(state: dict, response: dict, client: OllamaClient | None, latency_ms: int) -> None:
    """Store what happened, without names, marks, attendance values or model reasoning."""
    retrieved = [{"chunk_id": h["chunk_id"], "score": h["score"]} for h in state.get("hits", [])[:12]]
    tools_summary = [{"tool": c["tool"], "input": c["input"], "status": c["status"],
                      "result": c["output"].get("result", "")} for c in state.get("tool_calls", [])]
    database.insert_audit({
        "trace_id": response["trace_id"],
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "student_id": state.get("auth_student_id"),
        "as_of_date": response["as_of_date"],
        "question_type": state.get("question_type"),
        "answer_type": response["answer_type"],
        "retrieved": json.dumps(retrieved),
        "selected_documents": json.dumps(sorted({c["doc_id"] for c in response["citations"]})),
        "precedence_decision": json.dumps(state.get("policy_log", [])),
        "tools": json.dumps(tools_summary),
        "applied_rules": json.dumps([r["rule_id"] for r in response["applied_rules"]]),
        "citation_check": state.get("citation_check", ""),
        "flagged_content": int(any(h["metadata"].get("flagged") for h in state.get("evidence", []))),
        "model": "mock" if client is None else client.model,
        "llm_calls": client.calls if client else 0,
        "latency_ms": latency_ms,
    })
