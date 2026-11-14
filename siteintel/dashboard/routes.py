from flask import abort, render_template
from flask_login import current_user, login_required

from siteintel.dashboard import dashboard_bp
from siteintel.extensions import db
from siteintel.ml.models import DocumentPrediction, PredictionHead
from siteintel.projects import ConstructionRecord, Document, Project, get_accessible_project


@dashboard_bp.get("/")
@login_required
def index():
    projects = db.session.scalars(
        Project.accessible_to(current_user.id).order_by(Project.updated_at.desc(), Project.id.desc())
    ).all()
    project_ids = [project.id for project in projects]
    documents = db.session.scalars(
        db.select(Document).where(Document.project_id.in_(project_ids))
        .order_by(Document.created_at.desc(), Document.id.desc()).limit(5)
    ).all()
    report_count = db.session.scalar(
        db.select(db.func.count(Document.id)).where(Document.project_id.in_(project_ids))
    )
    prediction_count = db.session.scalar(
        db.select(db.func.count(DocumentPrediction.id))
        .join(PredictionHead, PredictionHead.prediction_id == DocumentPrediction.id).join(Document)
        .where(Document.project_id.in_(project_ids), DocumentPrediction.status == "complete")
    )
    records = db.session.scalars(
        db.select(ConstructionRecord).where(ConstructionRecord.project_id.in_(project_ids))
        .order_by(ConstructionRecord.created_at.desc(), ConstructionRecord.id.desc()).limit(5)
    ).all()
    activities = sorted(
        [(document.created_at, f"Report uploaded: {document.file_name}", document.project.name) for document in documents]
        + [(record.created_at, record.title, record.project.name) for record in records],
        key=lambda item: item[0], reverse=True,
    )[:5]
    return render_template(
        "dashboard.html", user=current_user, projects=projects,
        report_count=report_count, prediction_count=prediction_count, activities=activities,
    )


@dashboard_bp.get("/ml/")
@login_required
def ml_predictions():
    return render_template("ml_predictions.html")


@dashboard_bp.get("/projects/<int:project_id>/")
@login_required
def project_workspace(project_id):
    project = get_accessible_project(current_user.id, project_id)
    if project is None:
        abort(404)
    return render_template("project_workspace.html", project=project)
