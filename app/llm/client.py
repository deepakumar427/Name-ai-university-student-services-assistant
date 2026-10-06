"""Small Ollama client. The LLM only understands language and writes text; it never decides policy."""

import json
import logging
import re

import httpx

from app.config import settings

log = logging.getLogger(__name__)


class LLMError(Exception):
    """Ollama is unavailable, timed out, or returned something unusable."""


class OllamaClient:
    def __init__(self) -> None:
        self.base_url = settings.ollama_base_url
        self.model = settings.ollama_model
        self.timeout = settings.ollama_timeout
        self.calls = 0

    def generate(self, system: str, prompt: str, json_mode: bool = False) -> str:
        payload = {
            "model": self.model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0, "seed": 7, "num_ctx": 4096},
            "keep_alive": "30m",
        }
        if json_mode:
            payload["format"] = "json"
        self.calls += 1
        try:
            resp = httpx.post(f"{self.base_url}/api/generate", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama timed out after {self.timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Ollama unavailable: {exc}") from exc
        text = resp.json().get("response", "")
        if not text.strip():
            raise LLMError("Ollama returned an empty response")
        return text.strip()

    def generate_json(self, system: str, prompt: str) -> dict:
        """Ask for JSON; retry once if the output can't be parsed."""
        last_error = ""
        for _ in range(2):
            raw = self.generate(system, prompt, json_mode=True)
            parsed = parse_json(raw)
            if parsed is not None:
                return parsed
            last_error = raw[:200]
        raise LLMError(f"Malformed JSON from LLM: {last_error}")


def parse_json(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def ollama_status() -> str:
    """'ok', 'model_missing', 'down' or 'mock'."""
    if settings.mock_llm:
        return "mock"
    try:
        resp = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=3)
        resp.raise_for_status()
    except httpx.HTTPError:
        return "down"
    names = {m.get("name", "") for m in resp.json().get("models", [])}
    wanted = settings.ollama_model
    if wanted in names or f"{wanted}:latest" in names:
        return "ok"
    return "model_missing"
