import uuid
from datetime import datetime
from typing import Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.security import BCRYPT_MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8
MAX_EMAIL_LENGTH = 320


def _check_password_bytes(value: str) -> str:
    if len(value.encode()) > BCRYPT_MAX_PASSWORD_BYTES:
        raise ValueError(f"must be at most {BCRYPT_MAX_PASSWORD_BYTES} bytes")
    return value


class LoginRequest(BaseModel):
    # Deliberately not validated as an email: a malformed address is just another failed
    # login, answered with the same generic message (FR-01-03).
    email: str = Field(min_length=1, max_length=MAX_EMAIL_LENGTH)
    # No minimum here: the login form must not hint at password rules.
    password: str = Field(min_length=1)

    _password_bytes = field_validator("password")(_check_password_bytes)


class OperatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    is_active: bool
    last_login_at: datetime | None


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105  # OAuth2 token type, not a secret
    expires_at: datetime
    operator: OperatorOut


def validate_new_password(password: str) -> str:
    """Rules for setting a password (account creation). Raises ValueError with the reason."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    _check_password_bytes(password)
    if password.strip() != password:
        raise ValueError("Password must not start or end with spaces.")
    return password


def validate_new_email(email: str) -> str:
    """Check and normalize an email for a new account. Raises ValueError with the reason.

    Reserved test domains (`.test`, `example.com`) are accepted so demo and seed accounts work.
    """
    try:
        result = validate_email(email.strip(), check_deliverability=False, test_environment=True)
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email: {exc}") from exc
    if len(result.normalized) > MAX_EMAIL_LENGTH:
        raise ValueError(f"Email must be at most {MAX_EMAIL_LENGTH} characters.")
    return result.normalized.lower()
