import re
import unicodedata
from collections.abc import Mapping
from typing import Any


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128


class ValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("Input validation failed.")
        self.errors = errors


def _read_string(
    payload: Mapping[str, Any], field: str, errors: dict[str, str]
) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        errors[field] = "This field is required and must be text."
        return ""
    return value.strip()


def _validate_email(value: str, errors: dict[str, str]) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        errors["email"] = "Enter a valid email address."
    return email


def validate_registration(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValidationError({"request": "A JSON object or form fields are required."})

    errors: dict[str, str] = {}
    full_name = _read_string(payload, "full_name", errors)
    email = _validate_email(_read_string(payload, "email", errors), errors)
    password_value = payload.get("password")
    confirmation_value = payload.get("password_confirmation")

    if not 2 <= len(full_name) <= 120:
        errors["full_name"] = "Full name must be between 2 and 120 characters."
    elif any(unicodedata.category(char).startswith("C") for char in full_name):
        errors["full_name"] = "Full name contains invalid characters."

    password = ""
    if not isinstance(password_value, str):
        errors["password"] = "Password is required and must be text."
    else:
        password = password_value
        if not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            errors["password"] = (
                f"Password must be between {PASSWORD_MIN_LENGTH} and "
                f"{PASSWORD_MAX_LENGTH} characters."
            )
        elif sum(
            (
                any(char.islower() for char in password),
                any(char.isupper() for char in password),
                any(char.isdigit() for char in password),
                any(not char.isalnum() for char in password),
            )
        ) < 3:
            errors["password"] = (
                "Password must include at least three of: lowercase, uppercase, "
                "number, and special character."
            )

    if not isinstance(confirmation_value, str):
        errors["password_confirmation"] = "Password confirmation is required."
    elif password != confirmation_value:
        errors["password_confirmation"] = "Passwords do not match."

    if errors:
        raise ValidationError(errors)
    return {"full_name": full_name, "email": email, "password": password}


def validate_login(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping):
        raise ValidationError({"request": "A JSON object or form fields are required."})

    errors: dict[str, str] = {}
    email = _validate_email(_read_string(payload, "email", errors), errors)
    password_value = payload.get("password")
    if (
        not isinstance(password_value, str)
        or not password_value
        or len(password_value) > PASSWORD_MAX_LENGTH
    ):
        errors["password"] = "Enter a valid password."

    if errors:
        raise ValidationError(errors)
    return {"email": email, "password": password_value}
