from typing import Mapping, Optional

import click
from flask import Flask, current_app, jsonify
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import HTTPException

from backend.shared.config import Config
from backend.shared.database import check_database_connection
from backend.shared.extensions import db, migrate


def create_app(test_config: Optional[Mapping[str, object]] = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = Config.database_uri()

    db.init_app(app)
    migrate.init_app(app, db)

    for blueprint in app.config.get("BLUEPRINTS", ()):
        app.register_blueprint(blueprint)

    @app.get("/health")
    def health():
        return jsonify(status="ok")

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        response = error.get_response()
        response.data = current_app.json.dumps(
            {
                "error": {
                    "code": error.code,
                    "message": error.description,
                }
            }
        )
        response.content_type = "application/json"
        return response

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        current_app.logger.exception("Unhandled application error")
        return jsonify(
            error={"code": 500, "message": "An internal server error occurred."}
        ), 500

    @app.cli.command("db-check")
    def db_check_command():
        """Check that the configured MySQL database is reachable."""
        try:
            check_database_connection()
        except SQLAlchemyError as error:
            current_app.logger.exception("MySQL connection check failed")
            raise click.ClickException(
                "MySQL connection failed. Verify the database is running and "
                "MYSQL_* settings are correct."
            ) from error
        click.echo("MySQL connection successful.")

    return app