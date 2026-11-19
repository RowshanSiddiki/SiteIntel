from typing import Mapping

import click
from flask import (
    Flask,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_wtf.csrf import CSRFError
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import RequestEntityTooLarge

from siteintel.config import Config
from siteintel.extensions import csrf, db, login_manager, migrate
from siteintel.ml.model_registry import load_model_registry


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
    app.config["MAX_CONTENT_LENGTH"] = (
        app.config.get("MAX_CONTENT_LENGTH") or 10 * 1024 * 1024
    )
    app.extensions["siteintel_model_registry"] = load_model_registry()

    db.init_app(app)
    migrate.init_app(app, db, compare_type=True)
    csrf.init_app(app)
    login_manager.init_app(app)

    from siteintel.auth import auth_bp
    from siteintel.dashboard import dashboard_bp
    from siteintel.documents import documents_bp
    from siteintel.expenses import expenses_bp
    from siteintel.ml.routes import ml_bp
    from siteintel.ml import models as ml_models
    from siteintel.projects import project_models
    from siteintel.reports import reports_bp
    from siteintel.reports import routes as report_routes
    from siteintel.reports import models as report_models

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(ml_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(expenses_bp)

    @app.get("/")
    def index():
        from flask_login import current_user

        if current_user.is_authenticated:
            return redirect(url_for("dashboard.index"))
        return redirect(url_for("auth.login"))

    @app.errorhandler(CSRFError)
    def handle_csrf_error(error: CSRFError):
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "error": {
                        "code": "csrf_failed",
                        "message": error.description,
                    }
                }
            ), 400
        return render_template("errors/csrf.html", reason=error.description, error_page=True), 400

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(error: RequestEntityTooLarge):
        current_app.logger.info(
            "Request exceeded the configured size limit (%s).",
            type(error).__name__,
        )
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "error": {
                        "code": "image_too_large",
                        "message": "The image exceeds the maximum upload size.",
                    }
                }
            ), 413
        return render_template(
            "errors/500.html",
            message="The uploaded file exceeds the maximum allowed size.",
            error_page=True,
        ), 413

    @app.errorhandler(SQLAlchemyError)
    def handle_database_error(error: SQLAlchemyError):
        current_app.logger.exception(
            "Database operation failed (%s)", type(error).__name__
        )
        try:
            db.session.rollback()
        except SQLAlchemyError:
            current_app.logger.exception("Session rollback failed")
        message = "A database error occurred. Please try again."
        try:
            return (
                render_template(
                    "errors/500.html", message=message, error_page=True
                ),
                500,
            )
        except Exception:
            # The error page must never raise a second error.
            current_app.logger.exception("Could not render the error page")
            return message, 500

    @app.cli.command("db-init")
    def db_init_command() -> None:
        """Create siteintel_db if needed, then create the application tables."""
        try:
            from siteintel.database.initialize import create_database_if_missing

            create_database_if_missing()
            with app.app_context():
                from flask_migrate import upgrade

                upgrade()
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

    @app.cli.command("finance-backfill")
    def finance_backfill_command() -> None:
        """Extract reviewable financial figures from already indexed documents."""
        from siteintel.documents.extraction import ExtractionError, extract_pdf_pages
        from siteintel.documents.storage import resolve_path
        from siteintel.expenses.extraction import sync_observations
        from siteintel.projects.models import Document
        from siteintel.reports.models import DailyConstructionReport

        rows = db.session.execute(
            db.select(Document, DailyConstructionReport.reporting_date)
            .outerjoin(DailyConstructionReport, (
                (DailyConstructionReport.document_id == Document.id)
                & (DailyConstructionReport.project_id == Document.project_id)
            ))
            .where(Document.category.is_not(None), Document.processing_status == "completed")
            .order_by(Document.id)
        ).all()
        failures = []
        for document, reporting_date in rows:
            path = resolve_path(document.storage_key, document.project_id)
            try:
                if path is None:
                    raise ExtractionError("The stored file could not be found.")
                pages = extract_pdf_pages(path).pages
                sync_observations(document, pages, reporting_date=reporting_date)
                db.session.commit()
            except (ExtractionError, OSError, SQLAlchemyError):
                db.session.rollback()
                app.logger.exception("Could not extract finance data from document %s", document.id)
                failures.append(document.id)
        if failures:
            raise click.ClickException(
                f"Financial extraction failed for {len(failures)} document(s); check server logs."
            )
        click.echo(f"Financial extraction completed for {len(rows)} indexed document(s).")

    return app


__all__ = ["create_app"]
