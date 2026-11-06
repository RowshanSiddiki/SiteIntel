from flask import Blueprint


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

from siteintel.auth import routes as routes

__all__ = ["auth_bp"]
