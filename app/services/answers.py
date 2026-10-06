"""Fixed answer templates and citation formatting. No LLM is used here, so these never hallucinate."""

import re

from app.security.citations import SENTENCE, words_in
from app.security.injection import PLACEHOLDER

NOT_FOUND = "I could not find this information in the authorised university sources."
REFUSED = ("I can't help with that request. Student records can only be shared with the signed-in student, "
           "about their own records.")
STEP_NAMES = {"supersession": "step 2: supersession", "authority": "step 3: authority", "recency": "step 4: recency"}


def body(chunk: dict) -> str:
    return chunk["text"].split("\n", 1)[-1]


def citation(chunk: dict, note: str = "") -> dict:
    m = chunk["metadata"]
    return {
        "doc_id": m["doc_id"],
        "title": m["title"],
        "section": str(m["section"]),
        "page": int(m["page"]) if m.get("page") not in (None, "") else None,
        "version": str(m["version"]),
        "effective_from": m["effective_from"],
        "note": note,
    }


ABBREVIATIONS = re.compile(r"\b(Rs|No|Nos|e\.g|i\.e|etc|Dr|Prof)\.", re.IGNORECASE)


def sentences_of(text: str) -> list[str]:
    """Split into sentences without breaking at 'Rs.' and without leaving clause titles on their own."""
    protected = ABBREVIATIONS.sub(lambda m: m.group(0)[:-1] + "\u2024", text)
    merged: list[str] = []
    for part in (p.strip() for p in SENTENCE.split(protected) if p.strip()):
        if merged and len(merged[-1]) < 40:  # e.g. "7.2 Minimum attendance." joins the sentence after it
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [m.replace("\u2024", ".") for m in merged]


def best_sentence(chunk: dict, question: str) -> str:
    """Pick the sentence of a chunk that overlaps most with the question (extractive fallback)."""
    sentences = [s for s in sentences_of(body(chunk)) if PLACEHOLDER not in s and not s.endswith("?")]
    if not sentences:
        return body(chunk).strip()
    q = words_in(question)
    return max(sentences, key=lambda s: (len(words_in(s) & q), -sentences.index(s)))


def extractive_answer(evidence: list[dict], question: str) -> str:
    return f"{best_sentence(evidence[0], question)} [{evidence[0]['label']}]"


def clarification(courses: list[dict], what: str) -> str:
    options = ", ".join(f"{c['course_code']} ({c['course_name']})" for c in courses) or "none on record"
    return f"Which course do you mean for {what}? Your courses: {options}."


def conflict_answer(chunks: list[dict], question: str) -> str:
    parts = [f"{c['metadata']['title']} ({c['metadata']['issuer']}) says: \"{best_sentence(c, question)}\""
             for c in chunks]
    offices = " or ".join(dict.fromkeys(c["metadata"]["issuer"] for c in chunks))
    return ("The authorised sources disagree and the official precedence rules cannot decide between them "
            "(same authority level, same effective date). " + " ".join(parts) +
            f" Please contact {offices} to confirm which applies.")


def explain_policy(governing: dict | None, conflicts: list[dict], upcoming: list[dict]) -> str:
    lines = []
    if governing:
        m = governing["metadata"]
        lines.append(f"Governing source: {m['title']} ({m['doc_id']}, authority level {m['authority_level']}, "
                     f"in force from {m['effective_from']}).")
    for c in conflicts:
        lines.append(f"Set aside {c['loser']}: {c['reason']} ({STEP_NAMES.get(c['step'], c['step'])}).")
    for chunk in upcoming:
        m = chunk["metadata"]
        lines.append(f"Upcoming change: {m['title']} ({m['doc_id']}) takes effect on {m['effective_from']}: "
                     f"\"{best_sentence(chunk, 'minimum required')}\"")
    return " ".join(lines)


def renumber(answer: str, label_to_index: dict[str, int]) -> str:
    """Replace evidence labels [E3] with citation numbers [1] the student can match to the source list."""
    return re.sub(r"\[(E\d+)\]", lambda m: f"[{label_to_index[m.group(1)]}]" if m.group(1) in label_to_index else "",
                  answer).strip()
