"""All settings in one place. Values come from environment variables (see .env.example)."""

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env reader so we don't need an extra dependency. Real env vars win."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].split("\t#", 1)[0]  # drop inline comments ("value   # note")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(PROJECT_ROOT / ".env")


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    documents_dir: Path
    db_path: Path
    chroma_dir: Path
    collection_name: str

    ollama_base_url: str
    ollama_model: str
    ollama_timeout: float
    mock_llm: bool
    llm_classify: bool
    llm_explain_calculated: bool

    retrieval_top_k: int
    evidence_k: int
    similarity_threshold: float
    conflict_margin: float
    chunk_size: int
    chunk_overlap: int

    max_question_chars: int
    max_upload_mb: int
    default_as_of_date: str  # empty means "today"

    jwt_secret: str
    jwt_secret_generated: bool  # True when JWT_SECRET was not set (tokens die on restart)
    jwt_expire_minutes: int
    bcrypt_rounds: int
    demo_student_password: str


def load_settings() -> Settings:
    jwt_secret = os.getenv("JWT_SECRET", "").strip()
    data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")))
    return Settings(
        data_dir=data_dir,
        documents_dir=Path(os.getenv("DOCUMENTS_DIR", str(PROJECT_ROOT / "data" / "documents"))),
        db_path=Path(os.getenv("DB_PATH", str(data_dir / "app.db"))),
        chroma_dir=Path(os.getenv("CHROMA_DIR", str(data_dir / "chroma"))),
        collection_name=os.getenv("CHROMA_COLLECTION", "university_docs"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b"),
        ollama_timeout=float(os.getenv("OLLAMA_TIMEOUT", "120")),
        mock_llm=_bool("MOCK_LLM", "false"),
        llm_classify=_bool("LLM_CLASSIFY", "true"),
        llm_explain_calculated=_bool("LLM_EXPLAIN_CALCULATED", "false"),
        retrieval_top_k=int(os.getenv("RETRIEVAL_TOP_K", "12")),
        evidence_k=int(os.getenv("EVIDENCE_K", "6")),
        similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.40")),
        conflict_margin=float(os.getenv("CONFLICT_MARGIN", "0.12")),
        chunk_size=int(os.getenv("CHUNK_SIZE", "1200")),
        chunk_overlap=int(os.getenv("CHUNK_OVERLAP", "150")),
        max_question_chars=int(os.getenv("MAX_QUESTION_CHARS", "1000")),
        max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "20")),
        default_as_of_date=os.getenv("DEFAULT_AS_OF_DATE", ""),
        jwt_secret=jwt_secret or secrets.token_urlsafe(48),
        jwt_secret_generated=not jwt_secret,
        jwt_expire_minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "60")),
        bcrypt_rounds=int(os.getenv("BCRYPT_ROUNDS", "12")),
        demo_student_password=os.getenv("DEMO_STUDENT_PASSWORD", "Student@2026"),
    )


settings = load_settings()
