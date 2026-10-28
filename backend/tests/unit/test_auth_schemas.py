import pytest
from pydantic import ValidationError

from app.auth.schemas import LoginRequest, validate_new_email, validate_new_password


def test_login_accepts_any_email_text() -> None:
    # Malformed emails must fail like any other bad login, not with a validation error.
    request = LoginRequest(email="not-an-email", password="x")

    assert request.email == "not-an-email"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "", "password": "x"},
        {"email": "a@example.com", "password": ""},
        {"email": "a@example.com", "password": "é" * 37},  # 74 bytes
    ],
    ids=["empty-email", "empty-password", "over-72-bytes"],
)
def test_login_rejects_unusable_input(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        LoginRequest(**payload)


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("  Sara@Grand.Example.com ", "sara@grand.example.com"),
        ("owner@grand.test", "owner@grand.test"),
    ],
)
def test_new_email_is_normalized(raw: str, normalized: str) -> None:
    assert validate_new_email(raw) == normalized


@pytest.mark.parametrize("email", ["not-an-email", "a@", "@example.com", "a b@example.com"])
def test_new_email_rejects_invalid_addresses(email: str) -> None:
    with pytest.raises(ValueError, match="Invalid email"):
        validate_new_email(email)


@pytest.mark.parametrize(
    ("password", "reason"),
    [
        ("short", "at least 8"),
        (" padded-password ", "spaces"),
        ("é" * 40, "72 bytes"),
    ],
)
def test_new_password_rules(password: str, reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        validate_new_password(password)


def test_new_password_accepts_a_reasonable_password() -> None:
    assert validate_new_password("correct horse battery") == "correct horse battery"
