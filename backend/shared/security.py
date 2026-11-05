import os
from datetime import timedelta

from flask import Flask
from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()


def configure_security(app: Flask) -> None:
    secret_key = app.config.get("SECRET_KEY")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters.")

    secure_cookie = app.config.get(
        "SESSION_COOKIE_SECURE", os.getenv("SESSION_COOKIE_SECURE", "true")
    )
    if isinstance(secure_cookie, bool):
        secure_cookie_enabled = secure_cookie
    elif isinstance(secure_cookie, str) and secure_cookie.strip().lower() in {
        "true",
        "false",
    }:
        secure_cookie_enabled = secure_cookie.strip().lower() == "true"
    else:
        raise RuntimeError("SESSION_COOKIE_SECURE must be 'true' or 'false'.")

    app.config.update(
        SESSION_COOKIE_NAME="siteintel_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=secure_cookie_enabled,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        WTF_CSRF_CHECK_DEFAULT=True,
        WTF_CSRF_TIME_LIMIT=3600,
    )
    csrf.init_app(app)