import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import current_app, has_app_context

from backend.modules.agent.prompts import (
    TOOL_NAMES,
    explanation_messages,
    selection_messages,
)


logger = logging.getLogger(__name__)
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_RESPONSE_BYTES = 1_000_000


class AgentServiceError(RuntimeError):
    """Base error for LLM configuration and response failures."""


class LLMConfigurationError(AgentServiceError):
    """The LLM API is not configured safely."""


class LLMAPIError(AgentServiceError):
    """The LLM API request failed or returned an invalid response."""


class LLMClient(Protocol):
    def select_tool(self, question: str) -> dict[str, str]: ...

    def explain_result(
        self, question: str, tool_name: str, result: dict[str, Any]
    ) -> str: ...


class OpenAICompatibleClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @classmethod
    def from_environment(cls) -> "OpenAICompatibleClient":
        api_key = os.getenv("LLM_API_KEY", "").strip()
        if not api_key:
            raise LLMConfigurationError("LLM_API_KEY is not configured.")

        base_url = os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL).strip()
        parsed_url = urlparse(base_url)
        if (
            parsed_url.scheme != "https"
            and parsed_url.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            raise LLMConfigurationError(
                "LLM_BASE_URL must use HTTPS except for a local development server."
            )
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise LLMConfigurationError("LLM_BASE_URL is invalid.")
        if (
            parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise LLMConfigurationError(
                "LLM_BASE_URL cannot contain credentials, query, or fragment data."
            )

        try:
            timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
        except ValueError as error:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be numeric."
            ) from error
        if not 1 <= timeout <= 120:
            raise LLMConfigurationError(
                "LLM_TIMEOUT_SECONDS must be between 1 and 120."
            )

        return cls(
            api_key=api_key,
            model=os.getenv("LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            base_url=base_url,
            timeout=timeout,
        )

    def _complete(
        self, messages: list[dict[str, str]], *, json_mode: bool = False
    ) -> str:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        request = Request(
            f"{self._base_url}/chat/completions",
            data=json.dumps(payload, allow_nan=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise LLMAPIError("LLM API response exceeded the size limit.")
            decoded = json.loads(body)
            content = decoded["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMAPIError("LLM API returned an empty response.")
            return content.strip()
        except HTTPError as error:
            logger.warning("LLM API returned HTTP status %s", error.code)
            raise LLMAPIError("LLM API request failed.") from error
        except (URLError, TimeoutError, OSError) as error:
            logger.warning("LLM API connection failed: %s", type(error).__name__)
            raise LLMAPIError("LLM API is unavailable.") from error
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as error:
            raise LLMAPIError("LLM API returned an invalid response.") from error

    def select_tool(self, question: str) -> dict[str, str]:
        content = self._complete(selection_messages(question), json_mode=True)
        try:
            selection = json.loads(content)
        except json.JSONDecodeError as error:
            raise LLMAPIError("LLM returned an invalid tool selection.") from error
        if (
            not isinstance(selection, dict)
            or set(selection) != {"tool", "reason"}
            or not isinstance(selection["tool"], str)
            or selection["tool"] not in TOOL_NAMES
            or not isinstance(selection["reason"], str)
        ):
            raise LLMAPIError("LLM selected an invalid or unapproved tool.")
        return selection

    def explain_result(
        self, question: str, tool_name: str, result: dict[str, Any]
    ) -> str:
        return self._complete(
            explanation_messages(question, tool_name, result)
        )


def get_llm_client() -> LLMClient:
    if has_app_context():
        configured_client = current_app.config.get("AGENT_LLM_CLIENT")
        if configured_client is not None:
            return configured_client
    return OpenAICompatibleClient.from_environment()


def select_tool(question: str) -> dict[str, str]:
    return get_llm_client().select_tool(question)


def explain_result(
    question: str, tool_name: str, result: dict[str, Any]
) -> str:
    explanation = get_llm_client().explain_result(question, tool_name, result)
    if not isinstance(explanation, str) or not explanation.strip():
        raise LLMAPIError("LLM returned an empty explanation.")
    return explanation.strip()
