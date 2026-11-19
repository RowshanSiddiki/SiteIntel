from flask import Blueprint

expenses_bp = Blueprint("expenses", __name__, url_prefix="/projects/<int:project_id>/finance")

from siteintel.expenses import routes  # noqa: E402,F401
