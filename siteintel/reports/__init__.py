from flask import Blueprint


reports_bp = Blueprint("reports", __name__, url_prefix="/api/reports")

__all__ = ["reports_bp"]
