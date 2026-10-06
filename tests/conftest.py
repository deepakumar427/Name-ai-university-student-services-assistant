"""Test setup: every test module gets its own temporary data directory with the sample documents,
rules and synthetic students loaded. The LLM is mocked so results are deterministic and CI needs no Ollama.
"""

import os
import sys
from pathlib import Path

os.environ["MOCK_LLM"] = "true"  # must be set before the app modules read the settings
os.environ["DEFAULT_AS_OF_DATE"] = "2026-10-06"
os.environ["JWT_SECRET"] = "test-secret-not-for-production-0123456789abcdef"
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing in tests only
os.environ["DEMO_STUDENT_PASSWORD"] = "Test@Pass1"
TEST_PASSWORD = "Test@Pass1"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import database  # noqa: E402
from app.retrieval import store  # noqa: E402


def use_data_dir(path: Path) -> None:
    """Point the app at a fresh data directory (settings is frozen, so set the fields directly)."""
    object.__setattr__(settings, "data_dir", path)
    object.__setattr__(settings, "db_path", path / "app.db")
    object.__setattr__(settings, "chroma_dir", path / "chroma")
    store.reset_cache()


def build_environment(path: Path) -> None:
    import ingest_documents
    import load_students

    use_data_dir(path)
    database.init_db()
    assert ingest_documents.ingest_register(ROOT / "data" / "source_register.csv", ROOT / "data" / "documents") == 0
    assert ingest_documents.load_rules(ROOT / "data" / "rules.csv") == 0
    assert load_students.load(ROOT / "data" / "synthetic") == 0


@pytest.fixture(scope="module", autouse=True)
def environment(tmp_path_factory):
    build_environment(tmp_path_factory.mktemp("data"))
    yield


@pytest.fixture(scope="module")
def client(environment):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


_tokens: dict[tuple[int, str], str] = {}


def login(client, student_id: str, password: str = TEST_PASSWORD):
    return client.post("/auth/login", json={"student_id": student_id, "password": password})


def auth_headers(client, student_id: str = "S1001") -> dict:
    """Log in once per test module and student, like the Streamlit page does."""
    key = (id(client), student_id)
    if key not in _tokens:
        resp = login(client, student_id)
        assert resp.status_code == 200, resp.text
        _tokens[key] = resp.json()["access_token"]
    return {"Authorization": f"Bearer {_tokens[key]}"}


def ask(client, question: str, student_id: str = "S1001", as_of: str = "2026-10-06") -> dict:
    """Ask as an authenticated student (general questions use S1001; everyone must log in)."""
    resp = client.post("/ask", json={"question": question, "as_of_date": as_of}, headers=auth_headers(client, student_id))
    assert resp.status_code == 200, resp.text
    return resp.json()
