"""Queries that feed the main dashboard. Everything is computed from the database."""
from dataclasses import dataclass
from decimal import Decimal

from siteintel.extensions import db
from siteintel.projects import (
    PROJECT_STATUS_LABELS,
    ConstructionRecord,
    Document,
    Project,
)
from siteintel.reports.models import ReportReview

SORT_OPTIONS = {"newest": "Newest first", "oldest": "Oldest first"}
STATUS_FILTERS = {"": "All statuses", **PROJECT_STATUS_LABELS}


@dataclass(frozen=True)
class ProjectCard:
    project: Project
    progress: Decimal | None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def accessible_projects(user_id: int, query: str = "", status: str = "", sort: str = "newest"):
    statement = Project.accessible_to(user_id)
    query = query.strip()
    if query:
        statement = statement.where(Project.name.ilike(f"%{_escape_like(query)}%", escape="\\"))
    if status in PROJECT_STATUS_LABELS:
        statement = statement.where(Project.status == status)
    ordering = (Project.created_at.asc(), Project.id.asc()) if sort == "oldest" else (
        Project.created_at.desc(), Project.id.desc()
    )
    return db.session.scalars(statement.order_by(*ordering)).all()


def latest_progress(project_ids: list[int]) -> dict[int, Decimal]:
    """Latest recorded progress per project; projects without records are absent."""
    if not project_ids:
        return {}
    records = db.session.scalars(
        db.select(ConstructionRecord)
        .where(ConstructionRecord.project_id.in_(project_ids), ConstructionRecord.progress_percent.is_not(None))
        .order_by(ConstructionRecord.work_date.desc(), ConstructionRecord.id.desc())
    ).all()
    progress: dict[int, Decimal] = {}
    for record in records:
        progress.setdefault(record.project_id, record.progress_percent)
    return progress


def project_cards(projects: list[Project]) -> list[ProjectCard]:
    progress = latest_progress([project.id for project in projects])
    return [ProjectCard(project, progress.get(project.id)) for project in projects]


def project_stats(user_id: int) -> dict[str, int]:
    """Counts across every project the user can access, independent of any filter."""
    projects = db.session.scalars(Project.accessible_to(user_id)).all()
    return {
        "total": len(projects),
        "active": sum(project.status == "active" for project in projects),
        "in_progress": sum(project.status == "in_progress" for project in projects),
        "completed": sum(project.status == "completed" for project in projects),
    }


def pending_reviews(user_id: int, limit: int = 5):
    """Uploaded reports whose extracted information has not been confirmed yet."""
    accessible = Project.accessible_to(user_id).with_only_columns(Project.id)
    base = (
        Document.project_id.in_(accessible),
        Document.reports_only(),
        ~db.exists().where(ReportReview.document_id == Document.id),
    )
    total = db.session.scalar(db.select(db.func.count(Document.id)).where(*base))
    # Plain rows with the project name joined in, so rendering never lazy-loads.
    rows = db.session.execute(
        db.select(Document.id, Document.file_name, Document.project_id, Project.name)
        .join(Project, Project.id == Document.project_id)
        .where(*base)
        .order_by(Document.created_at.desc(), Document.id.desc())
        .limit(limit)
    ).all()
    documents = [
        {"id": r[0], "file_name": r[1], "project_id": r[2], "project_name": r[3]}
        for r in rows
    ]
    return total, documents
