"""Load the two OpenAI settings from the repository's local .env file."""

import os
from pathlib import Path


def load_env(path=None):
    path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.strip().partition("=")
        key = key.strip()
        if not separator or key not in (
            "OPENAI_API_KEY", "OPENAI_MODEL", "LLM_PROVIDER", "OLLAMA_MODEL", "OLLAMA_BASE_URL",
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)
