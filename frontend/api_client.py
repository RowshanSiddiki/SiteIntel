import http.cookiejar
import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit


class APIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class APIClient:
    def __init__(self, base_url: str | None = None, timeout: float = 15.0) -> None:
        configured_url = base_url or os.getenv(
            "SITEINTEL_API_URL", "http://127.0.0.1:5000"
        )
        self.base_url = configured_url.rstrip("/")
        parsed_url = urlsplit(self.base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("SITEINTEL_API_URL must be an HTTP(S) URL.")
        self.timeout = timeout
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._cookies)
        )

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    @staticmethod
    def _decode_response(body: bytes) -> dict[str, Any]:
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise APIError("The backend returned an invalid response.") from error
        if not isinstance(result, dict):
            raise APIError("The backend returned an invalid response.")
        return result

    @staticmethod
    def _api_error(payload: dict[str, Any], status_code: int) -> APIError:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            details = error.get("details")
        else:
            message = payload.get("message")
            details = None
        if not isinstance(message, str) or not message:
            message = f"The backend request failed (HTTP {status_code})."
        return APIError(message, status_code=status_code, details=details)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        csrf: bool = False,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if csrf:
            token_payload = self._request("GET", "/api/auth/csrf")
            token = token_payload.get("csrf_token")
            if not isinstance(token, str) or not token:
                raise APIError("The backend did not provide a CSRF token.")
            headers["X-CSRFToken"] = token

        request = urllib.request.Request(
            self._url(path), data=body, headers=headers, method=method
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                response_payload = self._decode_response(response.read())
        except urllib.error.HTTPError as error:
            try:
                error_payload = self._decode_response(error.read())
            except APIError:
                error_payload = {}
            raise self._api_error(error_payload, error.code) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise APIError(
                "Could not connect to the SiteIntel backend. "
                "Check that Flask is running and SITEINTEL_API_URL is correct."
            ) from error

        if not 200 <= response.status < 300:
            raise self._api_error(response_payload, response.status)
        return response_payload

    def register(
        self,
        *,
        full_name: str,
        email: str,
        password: str,
        password_confirmation: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/auth/register",
            payload={
                "full_name": full_name,
                "email": email,
                "password": password,
                "password_confirmation": password_confirmation,
            },
            csrf=True,
        )

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/auth/login",
            payload={"email": email, "password": password},
            csrf=True,
        )

    def logout(self) -> dict[str, Any]:
        try:
            return self._request("POST", "/api/auth/logout", payload={}, csrf=True)
        finally:
            self.clear_session()

    def current_user(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/auth/me")
        user = payload.get("user")
        if not isinstance(user, dict):
            raise APIError("The backend returned an invalid user session.")
        return user

    def dashboard_stats(self) -> dict[str, Any]:
        payload = self._request("GET", "/api/dashboard/stats")
        stats = payload.get("stats")
        if not isinstance(stats, dict):
            raise APIError("The backend returned invalid dashboard statistics.")
        return stats

    def assistant_chat(
        self,
        *,
        question: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"question": question}
        if inputs is not None:
            payload["inputs"] = inputs
        return self._request(
            "POST", "/api/assistant/chat", payload=payload, csrf=True
        )

    def clear_session(self) -> None:
        self._cookies.clear()
