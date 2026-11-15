"""Minimal OpenAI-compatible chat client built on the standard library."""
import json
import logging
import urllib.error
import urllib.request

from siteintel.documents.settings import LLMSettings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """The language model could not produce a usable response."""


def generate(settings: LLMSettings, system: str, user: str) -> str:
    payload = json.dumps({
        "model": settings.model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # Providers behind Cloudflare (e.g. Groq) reject urllib's default
            # "Python-urllib" agent with HTTP 403 / error 1010.
            "User-Agent": "SiteIntel/1.0",
            "Authorization": f"Bearer {settings.api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.timeout) as response:  # noqa: S310 - scheme validated in settings
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as error:
        try:
            detail = error.read(300).decode("utf-8", "replace")
        except OSError:
            detail = ""
        logger.error("LLM provider returned HTTP %s: %s", error.code, detail)
        raise LLMError("The language model request failed.") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        logger.error("LLM provider unreachable (%s)", type(error).__name__)
        raise LLMError("The language model could not be reached.") from error
    except (ValueError, KeyError, IndexError, TypeError) as error:
        logger.error("LLM provider returned an unexpected response")
        raise LLMError("The language model returned an unexpected response.") from error
    if not isinstance(content, str) or not content.strip():
        raise LLMError("The language model returned an empty answer.")
    return content.strip()
