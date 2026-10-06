"""Turn the synthetic sample documents in data/documents/source_text/*.txt into PDFs.

Uses the macOS `cupsfilter` command, so it only needs to run on a developer Mac.
The generated PDFs are committed, so nobody else needs to run this.
A line containing only <<<PAGE>>> starts a new PDF page.

Usage: python scripts/make_sample_pdfs.py
"""

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "documents" / "source_text"
OUT = ROOT / "data" / "documents"


def to_paged_text(raw: str) -> str:
    pages = []
    for page in raw.split("<<<PAGE>>>"):
        lines = []
        for para in page.strip().split("\n"):
            lines.extend(textwrap.wrap(para, width=78) or [""])
        pages.append("\n".join(lines))
    return "\n\f".join(pages) + "\n"


def main() -> int:
    for src in sorted(SRC.glob("*.txt")):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tmp:
            tmp.write(to_paged_text(src.read_text()))
        pdf = OUT / f"{src.stem}.pdf"
        with pdf.open("wb") as fh:
            subprocess.run(["cupsfilter", tmp.name], stdout=fh, stderr=subprocess.DEVNULL, check=True)
        print(f"wrote {pdf.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
