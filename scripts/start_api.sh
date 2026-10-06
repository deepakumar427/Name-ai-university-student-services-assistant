#!/bin/sh
# Container start: prepare data once, then serve the API.
# Ingestion skips documents that are already registered, so restarts do not re-ingest anything.
set -e
python scripts/ingest_documents.py
python scripts/load_students.py --dir data/synthetic
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
