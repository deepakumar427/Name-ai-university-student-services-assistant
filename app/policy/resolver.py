"""Builds claims from rule_registry rows and retrieved chunks, runs the precedence engine,
and returns the evidence that is allowed to reach the answer. Plain Python, no LLM.
"""

from dataclasses import dataclass, field

from app.config import settings
from app.db import database
from app.policy.precedence import Claim, Resolution, Scope, applies, resolve, superseded_by, supersession_map
from app.policy.topics import PARAMETERS, extract_values, rule_values
from app.retrieval import store


@dataclass
class PolicyOutcome:
    status: str  # "ok" | "flagged" | "not_available" | "unstructured"
    evidence: list[dict] = field(default_factory=list)  # chunks allowed into the answer, best first
    conflicts: list[dict] = field(default_factory=list)
    upcoming: list[dict] = field(default_factory=list)  # chunks of documents that start later
    flagged_chunks: list[dict] = field(default_factory=list)
    rule: dict | None = None  # winning rule_registry row (parameter questions only)
    rule_chunks: list[dict] = field(default_factory=list)  # the clause the winning rule/claim comes from
    governing_doc: str = ""
    log: list[str] = field(default_factory=list)


def _documents() -> list[dict]:
    return database.all_documents()


def _chunk_claim(hit: dict, values: frozenset[str]) -> Claim:
    m = hit["metadata"]
    return Claim(
        claim_id=hit["chunk_id"],
        doc_id=m["doc_id"],
        section=str(m["section"]),
        authority_level=int(m["authority_level"]),
        effective_from=m["effective_from"],
        effective_to=m.get("effective_to", ""),
        scope_programmes=m.get("scope_programmes", "ALL"),
        scope_batches=m.get("scope_batches", "ALL"),
        values=values,
        payload={"kind": "text", "chunk": hit},
    )


def _rule_claim(rule: dict, doc: dict) -> Claim:
    return Claim(
        claim_id=rule["rule_id"],
        doc_id=rule["source_doc_id"],
        section=rule["source_section"],
        authority_level=int(doc["authority_level"]),
        effective_from=rule["effective_from"],
        effective_to=rule.get("effective_to") or "",
        scope_programmes=rule.get("scope_programmes") or "ALL",
        scope_batches=rule.get("scope_batches") or "ALL",
        values=rule_values(rule["value"]),
        payload={"kind": "rule", "rule": rule},
    )


def _conflicts(res: Resolution) -> list[dict]:
    doc_of = {c.claim_id: c.doc_id for c in res.winners + [loser.claim for loser in res.losers]}
    return [
        {
            "winner": doc_of.get(loser.winner_id, loser.winner_id),
            "loser": f"{loser.claim.doc_id}#{loser.claim.section}",
            "step": loser.step,
            "reason": loser.reason,
        }
        for loser in res.losers
    ]


def _claim_chunks(claim: Claim) -> list[dict]:
    if claim.payload["kind"] == "text":
        return [claim.payload["chunk"]]
    return store.get_section_chunks(claim.doc_id, claim.section)


def _upcoming_chunks(res: Resolution) -> list[dict]:
    # Only official circulars/regulations (level 1-2) are worth announcing as upcoming changes.
    chunks = []
    for claim in res.upcoming:
        if claim.authority_level <= 2 and claim.values:
            chunks.extend(_claim_chunks(claim))
    return chunks


def allowed_chunks(hits: list[dict], documents: list[dict], scope: Scope, as_of: str) -> list[dict]:
    """Drop chunks that are not in force, out of scope, unofficial (level 5) or superseded."""
    entries = supersession_map(documents, scope, as_of)
    kept = []
    for hit in hits:
        claim = _chunk_claim(hit, frozenset())
        if claim.authority_level >= 5 or not applies(claim, scope, as_of) or superseded_by(claim, entries):
            continue
        kept.append(hit)
    return kept


def resolve_parameter(parameter: str, scope: Scope, as_of: str, hits: list[dict]) -> PolicyOutcome:
    """Find the governing value of one rule, e.g. min_attendance_pct, for this student and date."""
    spec = PARAMETERS.get(parameter, {"keywords": [], "value_kind": None})
    documents = _documents()
    docs_by_id = {d["doc_id"]: d for d in documents}

    claims: list[Claim] = []
    rule_docs = set()
    for rule in database.rules_for_parameter(parameter):
        doc = docs_by_id.get(rule["source_doc_id"])
        if doc:  # a rule whose source document is unknown can't be trusted
            claims.append(_rule_claim(rule, doc))
            rule_docs.add(rule["source_doc_id"])

    # Documents with no structured rule can still disagree (e.g. an FAQ, or a judge's new circular).
    best_text: dict[str, dict] = {}
    for hit in hits:
        doc_id = hit["metadata"]["doc_id"]
        text = hit["text"].lower()
        if doc_id in rule_docs or hit["score"] < settings.similarity_threshold:
            continue
        if not any(k in text for k in spec["keywords"]):
            continue
        if doc_id not in best_text or hit["score"] > best_text[doc_id]["score"]:
            best_text[doc_id] = hit
    for hit in best_text.values():
        values = extract_values(hit["text"], spec["value_kind"])
        if values:
            claims.append(_chunk_claim(hit, values))

    res = resolve(claims, documents, Scope(scope.programme, scope.batch_year), as_of)
    outcome = PolicyOutcome(status="ok", conflicts=_conflicts(res), upcoming=_upcoming_chunks(res), log=res.log)

    if res.flagged:
        outcome.status = "flagged"
        outcome.flagged_chunks = [ch for c in res.winners if c.values for ch in _claim_chunks(c)[:1]]
        return outcome
    if not res.winners:
        outcome.status = "not_available"
        return outcome

    winner = res.winners[0]
    outcome.governing_doc = winner.doc_id
    by_id = {h["chunk_id"]: h for h in hits}
    outcome.rule_chunks = [by_id.get(c["chunk_id"], {**c, "score": settings.similarity_threshold, "q_score": 0.0})
                           for c in _claim_chunks(winner)]
    outcome.evidence = list(outcome.rule_chunks)
    if winner.payload["kind"] == "rule":
        outcome.rule = winner.payload["rule"]
    else:
        outcome.status = "unstructured"  # governing text exists, but no rule row to calculate with

    # Supporting context: other allowed chunks, but never from a source that lost.
    losing = {loser.claim.claim_id for loser in res.losers}
    losing_docs = {loser.claim.doc_id for loser in res.losers if loser.step in {"authority", "recency"}}
    seen = {c["chunk_id"] for c in outcome.evidence}
    for hit in allowed_chunks(hits, documents, scope, as_of):
        if hit["score"] < settings.similarity_threshold or hit["chunk_id"] in seen:
            continue
        if hit["chunk_id"] in losing or hit["metadata"]["doc_id"] in losing_docs:
            continue
        outcome.evidence.append(hit)
        seen.add(hit["chunk_id"])
    # If the question is really about this rule (its best match is one of the rule's clauses), the winning
    # clause goes first. Otherwise (e.g. a question about condonation) the most relevant passage goes first.
    rule_clause_ids = {c["chunk_id"] for claim in claims for c in _claim_chunks(claim)}
    best = max(hits, key=lambda h: h.get("q_score", 0.0), default=None)
    about_rule = best is not None and best["chunk_id"] in rule_clause_ids
    ordered = sorted(outcome.evidence, key=lambda h: h.get("q_score", h["score"]), reverse=True)
    if about_rule:
        winner_ids = {c["chunk_id"] for c in outcome.rule_chunks}
        ordered = outcome.rule_chunks + [h for h in ordered if h["chunk_id"] not in winner_ids]
    outcome.evidence = ordered[: settings.evidence_k]
    return outcome


def resolve_general(hits: list[dict], scope: Scope, as_of: str) -> PolicyOutcome:
    """For questions not tied to a rule parameter: compare the top chunk of each relevant document."""
    documents = _documents()
    relevant = [h for h in hits if h["score"] >= settings.similarity_threshold]
    if not relevant:
        return PolicyOutcome(status="not_available")
    top_score = max(h["score"] for h in relevant)

    contenders: dict[str, dict] = {}
    for hit in relevant:
        doc_id = hit["metadata"]["doc_id"]
        if hit["score"] >= top_score - settings.conflict_margin and doc_id not in contenders:
            contenders[doc_id] = hit
    contender_ids = {h["chunk_id"] for h in contenders.values()}

    claims = [
        _chunk_claim(h, extract_values(h["text"], "general") if h["chunk_id"] in contender_ids else frozenset())
        for h in relevant
    ]
    res = resolve(claims, documents, scope, as_of)
    outcome = PolicyOutcome(status="ok", conflicts=_conflicts(res), upcoming=_upcoming_chunks(res), log=res.log)
    losing_docs = {loser.claim.doc_id for loser in res.losers if loser.step in {"authority", "recency"}}
    if res.flagged:
        disputed = [c for c in res.winners if c.values]
        best_doc = relevant[0]["metadata"]["doc_id"]
        if any(c.doc_id == best_doc for c in disputed):
            outcome.status = "flagged"
            outcome.flagged_chunks = [c.payload["chunk"] for c in disputed]
            return outcome
        # The best passage is not part of the dispute: answer from it, but keep the disputed sources out.
        losing_docs |= {c.doc_id for c in disputed}
        outcome.log.append(f"secondary sources {sorted(c.doc_id for c in disputed)} disagree; excluded from evidence")

    winners = [c.payload["chunk"] for c in res.winners if c.doc_id not in losing_docs]
    outcome.evidence = sorted(winners, key=lambda h: h["score"], reverse=True)[: settings.evidence_k]
    if not outcome.evidence:
        outcome.status = "not_available"
    else:
        outcome.governing_doc = outcome.evidence[0]["metadata"]["doc_id"]
    return outcome
