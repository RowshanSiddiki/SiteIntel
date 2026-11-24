"""Minimal OpenAI-compatible chat client built on the standard library."""
import json
import ipaddress
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from siteintel.documents.settings import LLMSettings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """The language model could not produce a usable response."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResponse:
    content: str | None
    tool_calls: list[ToolCall]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def generate(settings: LLMSettings, system: str, user: str, *, local_only: bool = False) -> str:
    if local_only:
        host = urllib.parse.urlparse(settings.base_url).hostname
        try:
            if not host or not ipaddress.ip_address(host).is_loopback:
                raise LLMError("The language model must use a numeric loopback address.")
        except ValueError as error:
            raise LLMError("The language model must use a numeric loopback address.") from error
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
        if local_only:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            response_context = opener.open(request, timeout=settings.timeout)
        else:
            response_context = urllib.request.urlopen(request, timeout=settings.timeout)  # noqa: S310 - scheme validated in settings
        with response_context as response:
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


def generate_with_tools(
    settings: LLMSettings,
    messages: list[dict],
    tools: list[dict],
) -> ToolResponse:
    """Request structured function calls from an OpenAI-compatible provider."""
    payload = json.dumps({
        "model": settings.model,
        "temperature": 0,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.base_url}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SiteIntel/1.0",
            "Authorization": f"Bearer {settings.api_key}",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirect())
        with opener.open(request, timeout=settings.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        message = body["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError("message must be an object")
        content = message.get("content")
        raw_calls = message.get("tool_calls", [])
        if content is not None and not isinstance(content, str):
            raise TypeError("message content must be text or null")
        if not isinstance(raw_calls, list):
            raise TypeError("tool_calls must be a list")
        tool_calls = []
        for raw_call in raw_calls:
            if not isinstance(raw_call, dict) or not isinstance(raw_call.get("function"), dict):
                raise TypeError("tool call must contain a function object")
            function = raw_call["function"]
            arguments = json.loads(function["arguments"])
            if (
                not isinstance(raw_call.get("id"), str)
                or not isinstance(function.get("name"), str)
                or not isinstance(arguments, dict)
            ):
                raise TypeError("invalid tool call")
            tool_calls.append(ToolCall(
                id=raw_call["id"],
                name=function["name"],
                arguments=arguments,
            ))
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
        logger.error("LLM provider returned an unexpected tool response")
        raise LLMError("The language model returned an unexpected response.") from error
    if not (content and content.strip()) and not tool_calls:
        raise LLMError("The language model returned an empty answer.")
    return ToolResponse(
        content=content.strip() if content and content.strip() else None,
        tool_calls=tool_calls,
    )
