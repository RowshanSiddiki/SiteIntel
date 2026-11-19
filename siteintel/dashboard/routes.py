import logging

from flask import abort, render_template, request
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from siteintel.dashboard import dashboard_bp
from siteintel.dashboard.services import (
    SORT_OPTIONS,
    STATUS_FILTERS,
    accessible_projects,
    latest_progress,
    pending_reviews,
    project_cards,
    project_stats,
)
from siteintel.extensions import db
from siteintel.projects import (
    PROJECT_STATUS_LABELS,
    get_accessible_project,
    get_owned_project,
)
from siteintel.reports.models import DailyConstructionReport

logger = logging.getLogger(__name__)

# slug -> (label, icon, template). The overview uses the bare workspace URL.
WORKSPACE_SECTIONS = {
    "ml-predictions": ("ML Predictions", "chart", "project/ml_predictions.html"),
    "report-builder": ("Report Builder", "file-text", "project/report_builder.html"),
    "document-assistant": ("Document Assistant", "file-search", "project/document_assistant.html"),
    "expenses": ("Expenses & Bills", "wallet", "project/expenses.html"),
    "ai-agent": ("AI Agent", "bot", "project/ai_agent.html"),
    "communication": ("Email & Communication", "mail", "project/communication.html"),
    "reports": ("Reports", "file-text", "project/reports.html"),
    "settings": ("Project Settings", "settings", "project/settings.html"),
}


@dashboard_bp.app_context_processor
def inject_notifications():
    def notifications():
        # Rendering an error page must never fail because this lookup failed.
        try:
            return pending_reviews(current_user.id)
        except SQLAlchemyError:
            logger.exception("Could not load notifications.")
            db.session.rollback()
            return 0, []

    return {"pending_reviews": notifications}


def _project_or_404(project_id):
    project = get_accessible_project(current_user.id, project_id)
    if project is None:
        abort(404)
    return project


def _workspace_context(project, section):
    return {
        "project": project,
        "section": section,
        "sections": WORKSPACE_SECTIONS,
        "status_labels": PROJECT_STATUS_LABELS,
        "can_edit": get_accessible_project(current_user.id, project.id, "editor") is not None,
    }


@dashboard_bp.get("/")
@login_required
def index():
    query = request.args.get("q", "").strip()[:160]
    status = request.args.get("status", "")
    sort = request.args.get("sort", "newest")
    if status not in STATUS_FILTERS:
        status = ""
    if sort not in SORT_OPTIONS:
        sort = "newest"
    return render_template(
        "dashboard.html",
        cards=project_cards(accessible_projects(current_user.id, query, status, sort)),
        stats=project_stats(current_user.id),
        query=query,
        status=status,
        sort=sort,
        status_filters=STATUS_FILTERS,
        sort_options=SORT_OPTIONS,
        status_labels=PROJECT_STATUS_LABELS,
        filtered=bool(query or status),
    )


@dashboard_bp.get("/settings/")
@login_required
def account_settings():
    return render_template("account_settings.html")


@dashboard_bp.get("/help/")
@login_required
def help_page():
    return render_template("help.html")


@dashboard_bp.get("/ml/")
@login_required
def ml_predictions():
    return render_template("ml_predictions.html")


@dashboard_bp.get("/projects/<int:project_id>/")
@login_required
def project_workspace(project_id):
    project = _project_or_404(project_id)
    progress = latest_progress([project.id]).get(project.id)
    return render_template(
        "project/overview.html", progress=progress, **_workspace_context(project, "overview")
    )


@dashboard_bp.get("/projects/<int:project_id>/report-builder/<int:report_id>/")
@login_required
def daily_report_detail(project_id, report_id):
    project = _project_or_404(project_id)
    report = db.session.scalar(
        db.select(DailyConstructionReport).where(
            DailyConstructionReport.id == report_id,
            DailyConstructionReport.project_id == project.id,
        )
    )
    if report is None:
        abort(404)
    return render_template(
        "project/daily_report.html",
        report=report,
        **_workspace_context(project, "report-builder"),
    )


@dashboard_bp.get("/projects/<int:project_id>/<section>/")
@login_required
def project_section(project_id, section):
    if section not in WORKSPACE_SECTIONS:
        abort(404)
    project = _project_or_404(project_id)
    context = _workspace_context(project, section)
    if section == "expenses":
        from siteintel.expenses.routes import overview
        context.update(overview(project))
    if section == "settings":
        context["can_manage"] = get_owned_project(current_user.id, project.id) is not None
    return render_template(WORKSPACE_SECTIONS[section][2], **context)
