import re
import unicodedata


EMAIL_PATTERN = re.compile(
    r"^[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+$",
    re.IGNORECASE,
)


def validate_full_name(value: str) -> str | None:
    name = value.strip()
    if not 2 <= len(name) <= 120:
        return "Full name must be between 2 and 120 characters."
    if any(unicodedata.category(char).startswith("C") for char in name):
        return "Full name contains invalid characters."
    return None


def validate_email(value: str) -> str | None:
    email = value.strip()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        return "Enter a valid email address."
    return None


def validate_password(value: str) -> str | None:
    if not 12 <= len(value) <= 128:
        return "Password must be between 12 and 128 characters."
    character_types = (
        any(char.islower() for char in value),
        any(char.isupper() for char in value),
        any(char.isdigit() for char in value),
        any(not char.isalnum() for char in value),
    )
    if sum(character_types) < 3:
        return (
            "Password must include at least three of: lowercase, uppercase, "
            "number, and special character."
        )
    return None
