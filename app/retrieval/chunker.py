"""Split pages into section-aware chunks so each citation points to one clause and one page."""

import re

from app.config import settings

# A heading line starts with a clause number like "7", "7.2" or "Q3", followed by text.
# Each number part has at most 2 digits, so years such as "2024." are not treated as headings.
HEADING = re.compile(r'^(?P<num>\d{1,2}(?:\.\d{1,2})*|Q\d{1,2})[.):]?\s+(?P<title>["(A-Z].*)$')


def _split_long(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start, end)
            end = cut if cut > start else end
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def chunk_pages(pages: list[tuple[int, str]], doc_id: str, title: str) -> list[dict]:
    """Return [{id, text, section, page}]. Text is prefixed with the title and section for context."""
    blocks: list[dict] = []  # {section, heading, page, lines}
    current = {"section": "preamble", "heading": "", "page": pages[0][0], "lines": []}
    parent_titles: dict[str, str] = {}

    for page_no, text in pages:
        if current["lines"]:  # a section continuing onto a new page becomes a new block
            blocks.append(current)
            current = {**current, "page": page_no, "lines": []}
        current["page"] = page_no
        for line in text.splitlines():
            match = HEADING.match(line.strip())
            if match:
                if current["lines"]:
                    blocks.append(current)
                num = match.group("num")
                parent_titles[num] = match.group("title").strip()
                current = {"section": num, "heading": line.strip(), "page": page_no, "lines": [line.strip()]}
            elif line.strip():
                current["lines"].append(line.strip())
    if current["lines"]:
        blocks.append(current)

    chunks, counters = [], {}
    for block in blocks:
        body = " ".join(block["lines"])
        # A bare heading such as "7. Attendance" is not a chunk; its title is added to its sub-clauses.
        if len(body) < 60 and block["section"] != "preamble" and "." not in block["section"]:
            continue
        top = block["section"].split(".")[0]
        context = f"{top}. {parent_titles[top]}" if top in parent_titles and top != block["section"] else ""
        for piece in _split_long(body, settings.chunk_size, settings.chunk_overlap):
            n = counters.get(block["section"], 0)
            counters[block["section"]] = n + 1
            header = f"[{title} | Section {block['section']}" + (f" | {context}" if context else "") + "]"
            chunks.append(
                {
                    "id": f"{doc_id}::{block['section']}::{block['page']}::{n}",
                    "text": f"{header}\n{piece}",
                    "section": block["section"],
                    "page": block["page"],
                }
            )
    return chunks
