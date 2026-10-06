"""Ingest every document listed in data/source_register.csv, then load data/rules.csv.

Already-ingested documents are skipped (reported as 'duplicate'), so running this twice is safe.

Usage: python scripts/ingest_documents.py [--register data/source_register.csv] [--rules data/rules.csv]
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import PROJECT_ROOT, settings  # noqa: E402
from app.db import database  # noqa: E402
from app.retrieval.ingest import OPERATORS, ingest_document  # noqa: E402

RULE_COLUMNS = [
    "rule_id", "description", "parameter", "operator", "value", "scope_programmes",
    "scope_batches", "effective_from", "effective_to", "source_doc_id", "source_section",
]


def ingest_register(register: Path, documents_dir: Path) -> int:
    failures = 0
    with register.open(newline="") as fh:
        for row in csv.DictReader(fh):
            file_name = row.pop("file_name")
            path = documents_dir / file_name
            if not path.exists():
                print(f"[rejected ] {row['doc_id']}: file not found: {path}")
                failures += 1
                continue
            meta = {k: v for k, v in row.items() if v != "" or k in {"effective_to", "supersedes"}}
            meta["authority_level"] = int(meta["authority_level"])
            result = ingest_document(file_name, path.read_bytes(), meta)
            print(f"[{result['status']:<9}] {result['doc_id']}: {result['chunks_indexed']} chunks "
                  f"{'; '.join(result['errors'] + result['warnings'])}")
            failures += result["status"] == "rejected"
    return failures


def load_rules(rules_csv: Path) -> int:
    failures = 0
    with rules_csv.open(newline="") as fh:
        for row in csv.DictReader(fh):
            problems = []
            if row["operator"] not in OPERATORS:
                problems.append(f"unknown operator {row['operator']}")
            if not database.get_document(row["source_doc_id"]):
                problems.append(f"source document {row['source_doc_id']} is not in the Source Register")
            if problems:
                print(f"[rule rejected] {row['rule_id']}: {'; '.join(problems)}")
                failures += 1
                continue
            database.upsert_rule({c: row.get(c, "") for c in RULE_COLUMNS})
            print(f"[rule loaded  ] {row['rule_id']} {row['parameter']} {row['operator']} {row['value']}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--register", default=str(PROJECT_ROOT / "data" / "source_register.csv"))
    parser.add_argument("--rules", default=str(PROJECT_ROOT / "data" / "rules.csv"))
    parser.add_argument("--documents-dir", default=str(settings.documents_dir))
    args = parser.parse_args()

    database.init_db()
    failures = ingest_register(Path(args.register), Path(args.documents_dir))
    failures += load_rules(Path(args.rules))
    print("done" if not failures else f"done with {failures} problem(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
