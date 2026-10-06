"""Which rule a question is about, and how to pull comparable values out of document text.

This file holds vocabulary only (words and search phrases). It contains NO thresholds:
every number used in a decision comes from rule_registry or from a cited document.
"""

import re

PARAMETERS: dict[str, dict] = {
    "min_attendance_pct": {
        "keywords": ["attendance"],
        "triggers": ["minimum", "required", "requirement", "need", "eligib", "enough", "sit", "appear",
                     "%", "percent", "how much", "allowed"],
        "query": "minimum attendance required to appear in the end-semester examination",
        "value_kind": "percent",
    },
    "supp_eligible_results": {
        "keywords": ["supplementary"],
        "triggers": ["eligib", "who can", "allowed", "can i"],
        "query": "eligibility for the supplementary examination failed absent detained",
        "value_kind": None,
    },
    "min_cgpa_placement": {
        "keywords": ["cgpa"],
        "triggers": ["placement"],
        "query": "minimum CGPA to register for campus placement",
        "value_kind": "decimal",
    },
    "max_active_backlogs_placement": {
        "keywords": ["backlog"],
        "triggers": ["placement"],
        "query": "active backlogs allowed to register for campus placement",
        "value_kind": None,
    },
}

PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|per\s?cent)", re.IGNORECASE)
MONEY = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+)", re.IGNORECASE)
DECIMAL = re.compile(r"\b(\d{1,2}\.\d{1,2})\b")


def detect_parameter(question: str) -> str | None:
    """Map a general policy question to a rule parameter, e.g. 'minimum attendance?' -> min_attendance_pct."""
    q = question.lower()
    for name, spec in PARAMETERS.items():
        if any(k in q for k in spec["keywords"]) and any(t in q for t in spec["triggers"]):
            return name
    return None


def number_text(value: str) -> str:
    """'80', '80.0' and '80.00' all become '80' so values compare reliably."""
    try:
        return f"{float(value.replace(',', '')):g}"
    except ValueError:
        return value.strip().upper()


def extract_values(text: str, kind: str | None) -> frozenset[str]:
    body = text.split("\n", 1)[-1]  # skip the "[title | section]" header line
    if kind == "percent":
        return frozenset(number_text(m) for m in PERCENT.findall(body))
    if kind == "decimal":
        return frozenset(number_text(m) for m in DECIMAL.findall(body))
    if kind == "general":
        found = {number_text(m) + "%" for m in PERCENT.findall(body)}
        found |= {"Rs" + number_text(m) for m in MONEY.findall(body)}
        return frozenset(found)
    return frozenset()


def rule_values(value: str) -> frozenset[str]:
    return frozenset(number_text(v) for v in value.split(";") if v.strip())
