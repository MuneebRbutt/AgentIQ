"""Password hashing and access tokens (FR-01-02, NFR03-01).

Passwords are hashed with bcrypt and never stored or logged in plain text. Access tokens
are short-lived JWTs that point at a server-side session row (`sid`), so logout and
account deactivation take effect immediately (FR-01-04) instead of waiting for expiry.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import bcrypt
import jwt

from app.core.config import Settings

# bcrypt only looks at the first 72 bytes; longer inputs are rejected at the API edge
# (see auth schemas) so two different long passwords can never share a hash.
BCRYPT_MAX_PASSWORD_BYTES = 72

_JWT_ALGORITHM = "HS256"
_JWT_ISSUER = "agentiq"
_JWT_AUDIENCE = "agentiq-dashboard"

# Verified against when the email is unknown, so a failed login takes the same time
# whether or not the account exists (FR-01-03: don't reveal which part was wrong).
_DUMMY_HASH = bcrypt.hashpw(b"agentiq-timing-equaliser", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-effort check. Pass `None` for an unknown account to burn equivalent time."""
    candidate = password.encode()
    if len(candidate) > BCRYPT_MAX_PASSWORD_BYTES:
        candidate = candidate[:BCRYPT_MAX_PASSWORD_BYTES]
        # A stored hash can never match an over-long password (the API rejects those).
        bcrypt.checkpw(candidate, _DUMMY_HASH.encode())
        return False
    if password_hash is None:
        bcrypt.checkpw(candidate, _DUMMY_HASH.encode())
        return False
    return bcrypt.checkpw(candidate, password_hash.encode())


@dataclass(frozen=True, slots=True)
class AccessTokenClaims:
    operator_id: uuid.UUID
    session_id: uuid.UUID
    expires_at: datetime


class InvalidTokenError(Exception):
    """The token is malformed, tampered with, expired, or not one of ours."""


def create_access_token(
    settings: Settings,
    *,
    operator_id: uuid.UUID,
    session_id: uuid.UUID,
    issued_at: datetime,
) -> tuple[str, datetime]:
    expires_at = issued_at + timedelta(minutes=settings.access_token_ttl_minutes)
    claims = {
        "sub": str(operator_id),
        "sid": str(session_id),
        "iss": _JWT_ISSUER,
        "aud": _JWT_AUDIENCE,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(claims, settings.jwt_secret_key.get_secret_value(), _JWT_ALGORITHM)
    return token, expires_at


def decode_access_token(settings: Settings, token: str, *, now: datetime) -> AccessTokenClaims:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[_JWT_ALGORITHM],
            issuer=_JWT_ISSUER,
            audience=_JWT_AUDIENCE,
            # Time claims are checked against the injected clock below, not the wall clock.
            options={
                "require": ["sub", "sid", "exp", "iat"],
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            },
        )
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=now.tzinfo)
        if expires_at <= now:
            raise InvalidTokenError("token expired")
        return AccessTokenClaims(
            operator_id=uuid.UUID(claims["sub"]),
            session_id=uuid.UUID(claims["sid"]),
            expires_at=expires_at,
        )
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise InvalidTokenError(str(exc)) from exc
