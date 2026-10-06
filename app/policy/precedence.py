"""Official Source Precedence Policy (hackathon guide, Annex A) as a pure, deterministic function.

The LLM never decides which document is official. Same inputs -> same output, always.

Steps:
  1. Applicability - valid on as_of_date and the scope covers the student. Future documents become
     "upcoming"; level-5 (unofficial) content is informational only.
  2. Supersession  - a level 1 or 2 document that is itself applicable replaces the document or clause
     it names in `supersedes` (e.g. "ACAD-REG-2024#7.2").
  3. Authority     - when claims disagree, a lower authority_level number (higher authority) wins.
  4. Recency       - same level: the later effective_from wins.
  5. Unresolved    - still disagreeing -> flagged (the caller returns conflict_flagged).
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Scope:
    """Who is asking. None for programme/batch means 'unknown': scope filters are not applied."""

    programme: str | None = None
    batch_year: int | None = None


@dataclass
class Claim:
    """One statement from one source: a rule_registry row or a document chunk.

    `values` holds the comparable values (e.g. {"80"}). Claims with no values never conflict.
    """

    claim_id: str
    doc_id: str
    section: str
    authority_level: int
    effective_from: str
    effective_to: str = ""
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    values: frozenset[str] = frozenset()
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class Loser:
    claim: Claim
    step: str  # "supersession" | "authority" | "recency"
    winner_id: str
    reason: str


@dataclass
class Resolution:
    winners: list[Claim] = field(default_factory=list)
    losers: list[Loser] = field(default_factory=list)
    upcoming: list[Claim] = field(default_factory=list)
    informational: list[Claim] = field(default_factory=list)
    excluded: list[tuple[Claim, str]] = field(default_factory=list)
    flagged: bool = False
    log: list[str] = field(default_factory=list)


# ---------- step 1 helpers ----------

def _split(value: str) -> list[str]:
    return [p.strip() for p in value.replace(",", ";").split(";") if p.strip()]


def programme_covered(scope_programmes: str, programme: str | None) -> bool:
    entries = _split(scope_programmes or "ALL")
    if programme is None or any(e.upper() == "ALL" for e in entries):
        return True
    student = programme.lower()
    return any(student == e.lower() or student.startswith(e.lower() + " ") for e in entries)


def batch_covered(scope_batches: str, batch_year: int | None) -> bool:
    entries = _split(scope_batches or "ALL")
    if batch_year is None or any(e.upper() == "ALL" for e in entries):
        return True
    for entry in entries:
        if entry.endswith("+") and entry[:-1].isdigit() and batch_year >= int(entry[:-1]):
            return True
        if "-" in entry:
            low, _, high = entry.partition("-")
            if low.isdigit() and high.isdigit() and int(low) <= batch_year <= int(high):
                return True
        if entry.isdigit() and int(entry) == batch_year:
            return True
    return False


def is_in_force(effective_from: str, effective_to: str, as_of: str) -> bool:
    return effective_from <= as_of and (not effective_to or effective_to >= as_of)


def applies(item: dict | Claim, scope: Scope, as_of: str) -> bool:
    get = item.get if isinstance(item, dict) else lambda k, d="": getattr(item, k, d)
    return (
        is_in_force(get("effective_from", ""), get("effective_to", "") or "", as_of)
        and programme_covered(get("scope_programmes", "ALL"), scope.programme)
        and batch_covered(get("scope_batches", "ALL"), scope.batch_year)
    )


# ---------- step 2 helper ----------

def supersession_map(documents: list[dict], scope: Scope, as_of: str) -> list[tuple[str, str | None, str]]:
    """[(target_doc_id, target_section or None, superseding_doc_id)] from applicable level 1-2 documents."""
    entries = []
    for doc in documents:
        if int(doc["authority_level"]) > 2 or not doc.get("supersedes") or not applies(doc, scope, as_of):
            continue
        for entry in _split(doc["supersedes"]):
            target, _, section = entry.partition("#")
            entries.append((target, section or None, doc["doc_id"]))
    return entries


def superseded_by(claim: Claim, entries: list[tuple[str, str | None, str]]) -> str | None:
    for target, section, by in entries:
        if claim.doc_id != target or by == claim.doc_id:
            continue
        if section is None or claim.section == section or claim.section.startswith(section + "."):
            return by
    return None


# ---------- the engine ----------

def ranked(claims: list[Claim]) -> list[Claim]:
    """Higher authority (lower number) first; within a level, most recent first."""
    newest_first = sorted(claims, key=lambda c: c.effective_from, reverse=True)
    return sorted(newest_first, key=lambda c: c.authority_level)


def resolve(claims: list[Claim], documents: list[dict], scope: Scope, as_of: str) -> Resolution:
    res = Resolution()

    # Step 1: applicability
    candidates: list[Claim] = []
    for claim in claims:
        if claim.effective_from > as_of:
            res.upcoming.append(claim)
            res.log.append(f"{claim.claim_id}: not yet in force (from {claim.effective_from}) -> upcoming (step 1)")
        elif not applies(claim, scope, as_of):
            res.excluded.append((claim, "not applicable on this date or to this student"))
            res.log.append(f"{claim.claim_id}: expired or out of scope -> excluded (step 1)")
        elif claim.authority_level >= 5:
            res.informational.append(claim)
            res.log.append(f"{claim.claim_id}: level 5 (unofficial) -> informational only")
        else:
            candidates.append(claim)

    # Step 2: explicit supersession by an applicable level 1-2 document
    entries = supersession_map(documents, scope, as_of)
    remaining: list[Claim] = []
    for claim in candidates:
        by = superseded_by(claim, entries)
        if by:
            reason = f"{by} explicitly supersedes {claim.doc_id}#{claim.section}"
            res.losers.append(Loser(claim, "supersession", by, reason))
            res.log.append(f"{by} supersedes {claim.doc_id}#{claim.section} (step 2)")
        else:
            remaining.append(claim)

    valued = [c for c in remaining if c.values]
    if len({c.values for c in valued}) <= 1:
        res.winners = ranked(remaining)
        return res

    # Step 3: authority
    best_level = min(c.authority_level for c in valued)
    top = [c for c in valued if c.authority_level == best_level]
    for claim in valued:
        if claim.authority_level > best_level and all(claim.values.isdisjoint(t.values) for t in top):
            winner = ranked(top)[0]
            res.losers.append(Loser(claim, "authority", winner.claim_id,
                                    f"level {winner.authority_level} {winner.doc_id} outranks "
                                    f"level {claim.authority_level} {claim.doc_id}"))
            res.log.append(f"{winner.claim_id} outranks {claim.claim_id} (step 3)")
    lost = {id(loser.claim) for loser in res.losers}

    # Step 4: recency among the highest authority
    latest = max(c.effective_from for c in top)
    newest = [c for c in top if c.effective_from == latest]
    for claim in top:
        if claim.effective_from < latest and all(claim.values.isdisjoint(n.values) for n in newest):
            res.losers.append(Loser(claim, "recency", newest[0].claim_id,
                                    f"{newest[0].doc_id} ({latest}) is newer than "
                                    f"{claim.doc_id} ({claim.effective_from})"))
            res.log.append(f"{newest[0].claim_id} is newer than {claim.claim_id} (step 4)")
            lost.add(id(claim))

    # Step 5: still disagreeing?
    surviving_values = {c.values for c in newest}
    if len(surviving_values) > 1:
        res.flagged = True
        res.log.append("unresolved: same authority and same date, different values (step 5)")

    res.winners = ranked([c for c in remaining if id(c) not in lost])
    return res
