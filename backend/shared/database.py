from sqlalchemy import text

from backend.shared.extensions import db


def check_database_connection() -> None:
    with db.engine.connect() as connection:
        connection.execute(text("SELECT 1"))