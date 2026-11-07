from flask import Blueprint


reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")

from siteintel.reports import routes as routes

__all__ = ["reports_bp"]
