from typing import Mapping

import click
from flask import Flask, current_app, redirect, render_template, url_for
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import SQLAlchemyError

from siteintel.config import Config
from siteintel.extensions import csrf, db, login_manager


def create_app(test_config: Mapping[str, object] | None = None) -> Flask:
    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    secret_key = app.config.get("SECRET_KEY")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError(
            "Set SECRET_KEY in the project .env file to a random value "
            "of at least 32 characters."
        )
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        app.config["SQLALCHEMY_DATABASE_URI"] = Config.database_uri()

    db.init_app(app)
    csrf.init_app(app)
    login_manager.init_app(app)

    from siteintel.auth import auth_bp
    from siteintel.dashboard import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)

    @app.get("/")
    def index():
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        return render_template("errors/csrf.html", reason=error.description), 400

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(error: SQLAlchemyError):
        current_app.logger.exception("Database operation failed")
        db.session.rollback()
        return (
            render_template(
                "errors/500.html",
                message="A database error occurred. Please try again.",
            ),
            500,
        )

    @app.cli.command("db-init")
    def db_init_command() -> None:
        """Create siteintel_db if needed, then create the application tables."""
        try:
            from siteintel.database.initialize import create_database_if_missing

            create_database_if_missing()
            with app.app_context():
                db.create_all()
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            app.logger.exception("Database initialization failed")
            raise click.ClickException(
                "Database initialization failed. Check MySQL is running and "
                "the MYSQL_* settings have the required privileges."
            ) from error
        click.echo("siteintel_db and application tables are ready.")

    @app.cli.command("db-check")
    def db_check_command() -> None:
        """Verify that the configured MySQL database is reachable."""
        try:
            with db.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
        except SQLAlchemyError as error:
            app.logger.exception("Database connection check failed")
            raise click.ClickException(
                "MySQL connection failed. Check MYSQL_* settings and server status."
            ) from error
        click.echo("MySQL connection successful.")

    return app


__all__ = ["create_app"]
