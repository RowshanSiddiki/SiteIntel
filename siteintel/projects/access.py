from siteintel.extensions import db
from siteintel.projects.models import Project


def get_accessible_project(
    user_id: int,
    project_id: int,
    permission: str = "viewer",
) -> Project | None:
    return db.session.scalar(
        Project.accessible_to(user_id, permission).where(Project.id == project_id)
    )


def get_owned_project(user_id: int, project_id: int) -> Project | None:
    return db.session.scalar(
        db.select(Project).where(
            Project.id == project_id,
            Project.owner_id == user_id,
        )
    )
