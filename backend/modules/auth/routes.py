from functools import wraps
from typing import Any, Callable, TypeVar, cast

from flask import Blueprint, current_app, g, jsonify, request, session
from flask_wtf.csrf import generate_csrf
from sqlalchemy.exc import IntegrityError

from backend.modules.auth.models import User
from backend.modules.auth.schemas import (
    ValidationError,
    validate_login,
    validate_registration,
)
from backend.modules.auth.service import (
    DuplicateEmailError,
    authenticate_user,
    register_user,
)
from backend.shared.extensions import db


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")
ViewFunction = TypeVar("ViewFunction", bound=Callable[..., Any])


def _payload() -> Any:
    if request.is_json:
        return request.get_json(silent=True)
    return request.form


def _user_json(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "created_at": user.created_at.isoformat(),
    }


def _error(message: str, status: int, *, details: dict[str, str] | None = None):
    body: dict[str, Any] = {"error": {"code": status, "message": message}}
    if details:
        body["error"]["details"] = details
    return jsonify(body), status


def login_required(view: ViewFunction) -> ViewFunction:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        user_id = session.get("user_id")
        if type(user_id) is not int:
            return _error("Authentication is required.", 401)

        user = db.session.get(User, user_id)
        if user is None:
            session.clear()
            return _error("Authentication is required.", 401)

        g.current_user = user
        return view(*args, **kwargs)

    return cast(ViewFunction, wrapped)


@auth_bp.get("/csrf")
def csrf_token():
    return jsonify(csrf_token=generate_csrf())


@auth_bp.post("/register")
def register():
    try:
        values = validate_registration(_payload())
    except ValidationError as error:
        return _error("Invalid registration data.", 400, details=error.errors)

    try:
        user = register_user(**values)
    except DuplicateEmailError:
        return _error("An account with this email already exists.", 409)
    except IntegrityError:
        current_app.logger.exception("Failed to persist a new user account")
        return _error("Unable to create the account.", 500)

    return jsonify(user=_user_json(user)), 201


@auth_bp.post("/login")
def login():
    try:
        values = validate_login(_payload())
    except ValidationError as error:
        return _error("Invalid login data.", 400, details=error.errors)

    user = authenticate_user(**values)
    if user is None:
        return _error("Invalid email or password.", 401)

    session.clear()
    session["user_id"] = user.id
    session.permanent = True
    return jsonify(user=_user_json(user))


@auth_bp.post("/logout")
def logout():
    session.clear()
    return jsonify(message="Logged out successfully.")


@auth_bp.get("/me")
@login_required
def current_user():
    return jsonify(user=_user_json(g.current_user))
