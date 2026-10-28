"""Domain errors and their single mapping to HTTP responses.

Services and agents raise `AppError` subclasses; routers never build error responses by
hand. Responses follow RFC 9457 (problem details) and never include stack traces or
internal messages, so clients can't learn about internals (FR-01-03, NFR02-02).
"""

from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

PROBLEM_JSON = "application/problem+json"

log = get_logger(__name__)


class AppError(Exception):
    """Base class for expected, client-facing errors."""

    status_code: int = HTTPStatus.BAD_REQUEST
    code: str = "bad_request"
    headers: dict[str, str] | None = None

    def __init__(self, detail: str, *, extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra or {}


class ValidationError(AppError):
    """Input that passed schema parsing but breaks a business rule."""

    status_code = HTTPStatus.UNPROCESSABLE_ENTITY
    code = "validation_error"


class NotFoundError(AppError):
    status_code = HTTPStatus.NOT_FOUND
    code = "not_found"


class ConflictError(AppError):
    status_code = HTTPStatus.CONFLICT
    code = "conflict"


class AuthenticationError(AppError):
    status_code = HTTPStatus.UNAUTHORIZED
    code = "unauthenticated"
    headers = {"WWW-Authenticate": "Bearer"}  # noqa: RUF012  # read-only class constant


class PermissionDeniedError(AppError):
    status_code = HTTPStatus.FORBIDDEN
    code = "forbidden"


def _problem(
    request: Request,
    status: int,
    code: str,
    detail: str,
    headers: dict[str, str] | None = None,
    **extra: Any,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"about:blank#{code}",
        "title": HTTPStatus(status).phrase,
        "status": status,
        "detail": detail,
        "code": code,
        "instance": request.url.path,
        **extra,
    }
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)  # noqa: S101  # registered for AppError only
    return _problem(request, exc.status_code, exc.code, exc.detail, exc.headers, **exc.extra)


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    detail = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return _problem(request, exc.status_code, code, detail)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    return _problem(
        request,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        "validation_error",
        "Request validation failed.",
        errors=errors,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled_error", path=request.url.path)
    return _problem(
        request,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "internal_error",
        "Something went wrong. Please try again.",
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
