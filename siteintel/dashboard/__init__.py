from flask import Blueprint


dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

from siteintel.dashboard import routes as routes

__all__ = ["dashboard_bp"]
