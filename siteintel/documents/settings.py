"""Runtime settings for the document intelligence module.

Values come from Flask config first, then environment variables, so the
credentials for the language model never live in source code.
"""
import os
from dataclasses import dataclass

from flask import current_app

MAX_QUESTION_CHARS = 1000
MIN_QUESTION_CHARS = 3


def _setting(name: str, default: str = "") -> str:
    configured = current_app.config.get(name)
    if configured not in (None, ""):
        return str(configured)
    return os.getenv(name, default).strip()


def _int_setting(name: str, default: int) -> int:
    try:
        value = int(_setting(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def max_upload_bytes() -> int:
    return _int_setting("DOCUMENT_MAX_BYTES", 25 * 1024 * 1024)


def max_pages() -> int:
    return _int_setting("DOCUMENT_MAX_PAGES", 300)


def max_ocr_pages() -> int:
    return _int_setting("DOCUMENT_MAX_OCR_PAGES", 20)


@dataclass(frozen=True)
class LLMSettings:
    api_key: str
    model: str
    base_url: str
    timeout: int


def llm_settings() -> LLMSettings | None:
    """Return the configured provider, or None when no LLM is set up.

    Any OpenAI-compatible chat-completions endpoint works (OpenAI, Azure
    gateways, local servers such as Ollama or vLLM).
    """
    model = _setting("LLM_MODEL")
    api_key = _setting("LLM_API_KEY")
    base_url = _setting("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not model or not api_key or not base_url.startswith(("http://", "https://")):
        return None
    return LLMSettings(api_key, model, base_url, _int_setting("LLM_TIMEOUT_SECONDS", 60))
