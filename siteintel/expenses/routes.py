from datetime import date

from flask import abort, flash, jsonify, redirect, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.orm import aliased

from siteintel.expenses import analytics, expenses_bp
from siteintel.extensions import db
from siteintel.projects import get_accessible_project
from siteintel.projects.models import Document, FinancialObservation


def _project(project_id, permission="viewer"):
    project = get_accessible_project(current_user.id, project_id, permission)
    if project is None:
        abort(404)
    return project


def _dates():
    dates = {}
    for key in ("start", "end"):
        value = request.args.get(key, "").strip()
        if value:
            try:
                dates[key] = date.fromisoformat(value)
            except ValueError:
                abort(400, "Enter valid filter dates.")
    if "start" in dates and "end" in dates and dates["start"] > dates["end"]:
        abort(400, "Start date must not be after end date.")
    return dates


def _expense_conditions(project_id, dates):
    """Confirmed invoice/direct-expense figures; identical duplicates count once."""
    duplicate = aliased(FinancialObservation)
    duplicate_document = aliased(Document)
    conditions = [
        FinancialObservation.project_id == project_id,
        FinancialObservation.status == "confirmed",
        Document.project_id == project_id,
        Document.processing_status == "completed",
        ~db.exists(db.select(duplicate.id).join(
            duplicate_document, duplicate_document.id == duplicate.document_id,
        ).where(
            duplicate.project_id == FinancialObservation.project_id,
            duplicate.status == "confirmed",
            duplicate_document.project_id == project_id,
            duplicate_document.processing_status == "completed",
            duplicate.id < FinancialObservation.id,
            duplicate.document_reference.is_not(None),
            duplicate.document_reference == FinancialObservation.document_reference,
            duplicate.kind == FinancialObservation.kind,
            duplicate.currency == FinancialObservation.currency,
            duplicate.amount == FinancialObservation.amount,
            duplicate.category == FinancialObservation.category,
            or_(duplicate.kind == "invoice_total", duplicate.label == FinancialObservation.label),
        )),
    ]
    if "start" in dates:
        conditions.append(FinancialObservation.observed_date >= dates["start"])
    if "end" in dates:
        conditions.append(FinancialObservation.observed_date <= dates["end"])
    return conditions


def chart_data(project, dates):
    return analytics.build(project.id, dates, _expense_conditions(project.id, dates))


def overview(project):
    pending = []
    if get_accessible_project(current_user.id, project.id, "editor") is not None:
        pending = db.session.scalars(
            db.select(FinancialObservation).join(Document).where(
                FinancialObservation.project_id == project.id,
                Document.project_id == project.id,
                Document.processing_status == "completed",
                FinancialObservation.status == "pending",
                FinancialObservation.kind.in_(("invoice_total", "direct_expense")),
                FinancialObservation.observed_date.is_not(None),
            ).order_by(FinancialObservation.id.desc())
        ).all()
    return dict(finance_pending=pending, finance_data=chart_data(project, _dates()))


@expenses_bp.get("/analytics.json")
@login_required
def analytics_json(project_id):
    return jsonify(chart_data(_project(project_id), _dates()))


@expenses_bp.post("/observations/<int:observation_id>/review")
@login_required
def review(project_id, observation_id):
    _project(project_id, "editor")
    decision = request.form.get("decision")
    if decision not in {"confirmed", "ignored", "pending"}:
        abort(400, "Choose a valid review decision.")
    eligible = ("confirmed", "ignored") if decision == "pending" else ("pending",)
    observation = db.session.scalar(
        db.select(FinancialObservation).join(Document).where(
            FinancialObservation.id == observation_id, FinancialObservation.project_id == project_id,
            Document.project_id == project_id, Document.processing_status == "completed",
            FinancialObservation.status.in_(eligible),
        ).with_for_update()
    )
    if observation is None:
        abort(404)
    observation.status = decision
    observation.reviewed_by_id = None if decision == "pending" else current_user.id
    db.session.commit()
    messages = {"confirmed": "Figure confirmed.", "ignored": "Figure ignored.",
                "pending": "Figure returned to review."}
    flash(messages[decision], "success")
    return redirect(url_for("dashboard.project_section", project_id=project_id, section="expenses"))
