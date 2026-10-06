"""Check that an answer is supported by its evidence before it reaches the student.

Checks (all deterministic):
- every evidence label like [E2] exists in the evidence we supplied;
- the answer uses at least one label;
- every number/date in a sentence appears in the evidence that sentence cites (or in tool output);
- each cited sentence shares real words with its evidence (stops invented statements);
- no other student's ID appears in the answer.
"""

import re

LABEL = re.compile(r"\[(E\d+)\]")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
SENTENCE = re.compile(r"(?<=[.!?\]])\s+")
STUDENT_ID = re.compile(r"\bS\d{4}\b")
STOP = set("""a an the of to in on for and or is are be by at as with from that this it its your you
your can may must will shall not no any all each per their they there was were has have had which who what
when where how do does if into than then so such these those about student students""".split())


def numbers_in(text: str) -> set[str]:
    out = set()
    for raw in NUMBER.findall(text):
        value = raw.replace(",", "")
        try:
            out.add(f"{float(value):g}")
        except ValueError:
            out.add(value)
    return out


def words_in(text: str) -> set[str]:
    """Content words, with a simple plural rule so 'certificates' matches 'certificate'."""
    words = {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in STOP}
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words}


def check_answer(answer: str, evidence: dict[str, str], extra_support: str = "") -> tuple[bool, str, list[str]]:
    """Return (ok, reason, labels_used). `evidence` maps label -> chunk text."""
    used = list(dict.fromkeys(LABEL.findall(answer)))
    if not used:
        return False, "answer has no evidence labels", []
    unknown = [lab for lab in used if lab not in evidence]
    if unknown:
        return False, f"answer cites unknown evidence {unknown}", used

    for sentence in [s for s in SENTENCE.split(answer.strip()) if s.strip()]:
        labels = LABEL.findall(sentence) or used  # an unlabelled sentence must be backed by the cited evidence
        support = " ".join(evidence[lab] for lab in labels if lab in evidence) + " " + extra_support
        claim = LABEL.sub("", sentence)
        missing = numbers_in(claim) - numbers_in(support)
        if missing:
            return False, f"number(s) {sorted(missing)} not found in the cited evidence", used
        claim_words = words_in(claim)
        if len(claim_words) >= 4 and len(claim_words & words_in(support)) < 2:
            return False, "a sentence is not supported by the cited evidence", used
    return True, "ok", used


def leaks_other_student(answer: str, allowed_id: str | None) -> bool:
    return any(found != (allowed_id or "") for found in STUDENT_ID.findall(answer))
