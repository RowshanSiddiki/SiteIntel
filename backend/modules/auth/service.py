from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from backend.modules.auth.models import User
from backend.shared.extensions import db


class DuplicateEmailError(ValueError):
    pass


def register_user(*, full_name: str, email: str, password: str) -> User:
    if db.session.scalar(select(User.id).where(User.email == email)) is not None:
        raise DuplicateEmailError

    user = User(
        full_name=full_name,
        email=email,
        password_hash=generate_password_hash(password, method="scrypt"),
    )
    db.session.add(user)
    try:
        db.session.commit()
    except IntegrityError as error:
        db.session.rollback()
        if db.session.scalar(select(User.id).where(User.email == email)) is not None:
            raise DuplicateEmailError from error
        raise
    return user


def authenticate_user(*, email: str, password: str) -> User | None:
    user = db.session.scalar(select(User).where(User.email == email))
    if user is None or not check_password_hash(user.password_hash, password):
        return None
    return user
