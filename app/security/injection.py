"""Find instruction-like text inside documents. Documents are DATA, never instructions.

This is only one layer of defence. Even if something slips through, the LLM cannot pick the
student, call tools, set the answer type, or get an unsupported number past the citation check.
"""

import re

PATTERNS = [
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (all |any )?(the )?(previous|prior|above|system) (instructions|rules|prompts?)",
    r"you are now (in )?\w+ mode",
    r"\b(act|behave) as (an? )?(admin|administrator|system|developer)",
    r"reveal (the )?(all |every )?(student|system|hidden|secret)",
    r"(system prompt|developer message)",
    r"tell (every|all) students? that",
    r"do not (cite|mention|tell)",
]
_REGEX = re.compile("|".join(PATTERNS), re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
PLACEHOLDER = "[instruction-like text removed]"


def is_suspicious(text: str) -> bool:
    return bool(_REGEX.search(text))


def sanitize(text: str) -> tuple[str, int]:
    """Replace instruction-like sentences with a placeholder. Returns (clean_text, removed_count)."""
    sentences = _SENTENCE.split(text)
    removed = 0
    kept = []
    for sentence in sentences:
        if is_suspicious(sentence):
            removed += 1
            if not kept or kept[-1] != PLACEHOLDER:
                kept.append(PLACEHOLDER)
        else:
            kept.append(sentence)
    return " ".join(kept), removed
