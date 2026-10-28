"""Operator authentication: login, logout, session resolution, account creation (FR-01).

Every login failure raises the same `AuthenticationError` message, whether the email is
unknown, the password is wrong, the account is disabled or temporarily locked (FR-01-03).
The real reason only goes to the server log.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Operator, OperatorSession
from app.auth.schemas import validate_new_email, validate_new_password
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError, ValidationError
from app.core.logging import get_logger
from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.tenancy.models import Tenant

log = get_logger(__name__)

INVALID_CREDENTIALS = "Invalid email or password."
INVALID_SESSION = "Your session has ended. Please log in again."


@dataclass(frozen=True, slots=True)
class LoginResult:
    access_token: str
    expires_at: datetime
    operator: Operator


@dataclass(frozen=True, slots=True)
class AuthContext:
    """Who is calling. The only trusted source of `tenant_id` for a request (NFR03-02)."""

    operator_id: uuid.UUID
    tenant_id: uuid.UUID
    session_id: uuid.UUID


def normalize_email(email: str) -> str:
    return email.strip().lower()


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings, clock: Clock) -> None:
        self._db = db
        self._settings = settings
        self._clock = clock

    async def login(self, email: str, password: str) -> LoginResult:
        now = self._clock.now()
        operator = await self._db.scalar(
            select(Operator).where(Operator.email == normalize_email(email))
        )

        # Always run bcrypt once, so response time doesn't reveal whether the account exists.
        password_ok = verify_password(password, operator.password_hash if operator else None)

        if operator is None:
            log.info("login_failed", reason="unknown_email")
            raise AuthenticationError(INVALID_CREDENTIALS)
        if not operator.is_active:
            log.info("login_failed", reason="inactive", operator_id=str(operator.id))
            raise AuthenticationError(INVALID_CREDENTIALS)
        if operator.locked_until is not None and operator.locked_until > now:
            log.info("login_failed", reason="locked", operator_id=str(operator.id))
            raise AuthenticationError(INVALID_CREDENTIALS)
        if not password_ok:
            await self._record_failed_attempt(operator, now)
            raise AuthenticationError(INVALID_CREDENTIALS)

        operator.failed_login_attempts = 0
        operator.locked_until = None
        operator.last_login_at = now

        session = OperatorSession(
            id=uuid.uuid4(),
            operator_id=operator.id,
            expires_at=now + timedelta(minutes=self._settings.access_token_ttl_minutes),
            last_seen_at=now,
        )
        self._db.add(session)
        await self._db.commit()

        token, expires_at = create_access_token(
            self._settings, operator_id=operator.id, session_id=session.id, issued_at=now
        )
        log.info("login_succeeded", operator_id=str(operator.id), tenant_id=str(operator.tenant_id))
        return LoginResult(access_token=token, expires_at=expires_at, operator=operator)

    async def _record_failed_attempt(self, operator: Operator, now: datetime) -> None:
        operator.failed_login_attempts += 1
        locked = operator.failed_login_attempts >= self._settings.login_max_failed_attempts
        if locked:
            operator.locked_until = now + timedelta(minutes=self._settings.login_lockout_minutes)
            operator.failed_login_attempts = 0
        await self._db.commit()
        log.info(
            "login_failed",
            reason="bad_password",
            operator_id=str(operator.id),
            locked=locked,
        )

    async def authenticate(self, token: str) -> AuthContext:
        """Resolve a bearer token to a live session, or raise `AuthenticationError`."""
        now = self._clock.now()
        try:
            claims = decode_access_token(self._settings, token, now=now)
        except InvalidTokenError as exc:
            log.info("token_rejected", reason=str(exc))
            raise AuthenticationError(INVALID_SESSION) from exc

        row = (
            await self._db.execute(
                select(OperatorSession.id, Operator.id, Operator.tenant_id)
                .join(Operator, OperatorSession.operator_id == Operator.id)
                .where(
                    OperatorSession.id == claims.session_id,
                    Operator.id == claims.operator_id,
                    OperatorSession.revoked_at.is_(None),
                    OperatorSession.expires_at > now,
                    Operator.is_active.is_(True),
                )
            )
        ).one_or_none()
        if row is None:
            log.info("token_rejected", reason="session_inactive")
            raise AuthenticationError(INVALID_SESSION)

        session_id, operator_id, tenant_id = row
        return AuthContext(operator_id=operator_id, tenant_id=tenant_id, session_id=session_id)

    async def logout(self, context: AuthContext) -> None:
        await self._db.execute(
            update(OperatorSession)
            .where(OperatorSession.id == context.session_id, OperatorSession.revoked_at.is_(None))
            .values(revoked_at=self._clock.now())
        )
        await self._db.commit()
        log.info("logout", operator_id=str(context.operator_id))

    async def create_operator(
        self, *, tenant: Tenant, email: str, full_name: str, password: str
    ) -> Operator:
        """Create an operator account (used by the setup command; no public sign-up, AS-4)."""
        try:
            normalized = validate_new_email(email)
            validate_new_password(password)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        existing = await self._db.scalar(select(Operator.id).where(Operator.email == normalized))
        if existing is not None:
            raise ConflictError("An operator with this email already exists.")

        operator = Operator(
            tenant_id=tenant.id,
            email=normalized,
            full_name=full_name.strip(),
            password_hash=hash_password(password),
            is_active=True,
            failed_login_attempts=0,
        )
        self._db.add(operator)
        await self._db.commit()
        return operator
