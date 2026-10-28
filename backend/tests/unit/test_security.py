import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.security import (
    BCRYPT_MAX_PASSWORD_BYTES,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
OPERATOR_ID = uuid.uuid4()
SESSION_ID = uuid.uuid4()


def _token(settings: Settings, issued_at: datetime = NOW) -> str:
    token, _ = create_access_token(
        settings, operator_id=OPERATOR_ID, session_id=SESSION_ID, issued_at=issued_at
    )
    return token


def test_password_hash_verifies_only_the_original_password() -> None:
    hashed = hash_password("correct-horse-1")

    assert hashed != "correct-horse-1"
    assert verify_password("correct-horse-1", hashed)
    assert not verify_password("correct-horse-2", hashed)


def test_hashes_are_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_unknown_account_and_overlong_password_never_verify() -> None:
    hashed = hash_password("a" * BCRYPT_MAX_PASSWORD_BYTES)

    assert not verify_password("anything", None)
    # bcrypt would ignore everything past 72 bytes; we refuse instead of matching.
    assert not verify_password("a" * (BCRYPT_MAX_PASSWORD_BYTES + 1), hashed)


def test_token_round_trip(settings: Settings) -> None:
    token, expires_at = create_access_token(
        settings, operator_id=OPERATOR_ID, session_id=SESSION_ID, issued_at=NOW
    )

    claims = decode_access_token(settings, token, now=NOW + timedelta(minutes=1))

    assert claims.operator_id == OPERATOR_ID
    assert claims.session_id == SESSION_ID
    assert claims.expires_at == expires_at
    assert expires_at == NOW + timedelta(minutes=settings.access_token_ttl_minutes)


def test_token_expiry_follows_the_given_clock(settings: Settings) -> None:
    token = _token(settings)
    expiry = NOW + timedelta(minutes=settings.access_token_ttl_minutes)

    decode_access_token(settings, token, now=expiry - timedelta(seconds=1))
    with pytest.raises(InvalidTokenError, match="expired"):
        decode_access_token(settings, token, now=expiry)


def test_token_signed_with_another_secret_is_rejected(settings: Settings) -> None:
    other = settings.model_copy(update={"jwt_secret_key": type(settings.jwt_secret_key)("x" * 40)})

    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, _token(other), now=NOW)


def test_tampered_token_is_rejected(settings: Settings) -> None:
    header, payload, signature = _token(settings).split(".")
    tampered = f"{header}.{payload}.{signature[::-1]}"

    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, tampered, now=NOW)


@pytest.mark.parametrize(
    "changes",
    [{"aud": "someone-else"}, {"iss": "someone-else"}, {"sid": None}, {"sub": "not-a-uuid"}],
    ids=["audience", "issuer", "no-session", "bad-subject"],
)
def test_token_with_wrong_claims_is_rejected(
    settings: Settings, changes: dict[str, object]
) -> None:
    claims = jwt.decode(_token(settings), options={"verify_signature": False})
    claims.update(changes)
    claims = {key: value for key, value in claims.items() if value is not None}
    forged = jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), "HS256")

    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, forged, now=NOW)


def test_unsigned_token_is_rejected(settings: Settings) -> None:
    claims = jwt.decode(_token(settings), options={"verify_signature": False})
    unsigned = jwt.encode(claims, key=None, algorithm="none")

    with pytest.raises(InvalidTokenError):
        decode_access_token(settings, unsigned, now=NOW)


def test_token_issued_by_a_clock_ahead_of_real_time_is_accepted(settings: Settings) -> None:
    future = datetime.now(UTC) + timedelta(days=30)
    token = _token(settings, issued_at=future)

    claims = decode_access_token(settings, token, now=future + timedelta(minutes=1))

    assert claims.session_id == SESSION_ID
