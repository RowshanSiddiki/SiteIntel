from flask import Blueprint


agent_bp = Blueprint(
    "agent",
    __name__,
    url_prefix="/api/projects/<int:project_id>/agent",
)

from siteintel.agent import routes as routes

__all__ = ["agent_bp"]
