import os

import pymysql

from siteintel.config import PROJECT_ROOT


DATABASE_NAME = "siteintel_db"


def create_database_if_missing() -> None:
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env", override=False)
    user = os.getenv("MYSQL_USER")
    password = os.getenv("MYSQL_PASSWORD")
    if not user or password is None:
        raise RuntimeError("MYSQL_USER and MYSQL_PASSWORD must be configured.")
    try:
        port = int(os.getenv("MYSQL_PORT", "3306"))
    except ValueError as error:
        raise RuntimeError("MYSQL_PORT must be an integer.") from error

    connection = pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{DATABASE_NAME}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()
