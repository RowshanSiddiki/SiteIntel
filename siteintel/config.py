import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _environment_bool(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name} must be 'true' or 'false'.")
    return value == "true"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    TEMPLATES_AUTO_RELOAD = True
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SESSION_COOKIE_NAME = "siteintel_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _environment_bool("SESSION_COOKIE_SECURE", "false")
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 8
    WTF_CSRF_TIME_LIMIT = 3600

    @classmethod
    def database_uri(cls) -> URL:
        user = os.getenv("MYSQL_USER")
        password = os.getenv("MYSQL_PASSWORD")
        if not user or password is None:
            raise RuntimeError(
                "Set MYSQL_USER and MYSQL_PASSWORD in the project .env file."
            )
        try:
            port = int(os.getenv("MYSQL_PORT", "3306"))
        except ValueError as error:
            raise RuntimeError("MYSQL_PORT must be an integer.") from error
        if not 1 <= port <= 65535:
            raise RuntimeError("MYSQL_PORT must be between 1 and 65535.")

        return URL.create(
            drivername="mysql+pymysql",
            username=user,
            password=password,
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=port,
            database="siteintel_db",
            query={"charset": "utf8mb4"},
        )
