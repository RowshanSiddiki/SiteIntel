import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    BLUEPRINTS = ()

    @classmethod
    def database_uri(cls) -> URL:
        user = os.getenv("MYSQL_USER")
        if not user:
            raise RuntimeError("MYSQL_USER must be set in the environment or .env file.")
        if "MYSQL_PASSWORD" not in os.environ:
            raise RuntimeError(
                "MYSQL_PASSWORD must be set in the environment or .env file."
            )

        try:
            port = int(os.getenv("MYSQL_PORT", "3306"))
        except ValueError as error:
            raise RuntimeError("MYSQL_PORT must be a valid integer.") from error

        return URL.create(
            drivername="mysql+pymysql",
            username=user,
            password=os.environ["MYSQL_PASSWORD"],
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=port,
            database="siteintel_db",
            query={"charset": "utf8mb4"},
        )