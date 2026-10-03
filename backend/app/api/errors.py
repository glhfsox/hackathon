from collections.abc import Sequence
from typing import Any

from fastapi import Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.deps import get_proxy_service
from app.models import PolicyError
from app.protocols.adapter import InvalidRequestError
from app.schemas.error_detail import ErrorDetail
from app.schemas.error_response import ErrorResponse
from app.schemas.field_errors import FieldErrors

# The agent-facing endpoints answer 400 in the OpenAI error shape.
AGENT_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
}
_AGENT_PREFIX = "/v1/"


def error_response(status_code: int, message: str, error_type: str) -> JSONResponse:
    """An error in the OpenAI shape, so stock clients can parse it."""
    error = ErrorResponse(error=ErrorDetail(message=message, type=error_type))
    return JSONResponse(error.model_dump(), status_code=status_code)


def unprocessable(errors: list[PolicyError]) -> JSONResponse:
    """422 of the dashboard API, `{"errors": [{loc, msg}]}`, the shape the policy editor reads."""
    return JSONResponse(
        FieldErrors(errors=errors).model_dump(), status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
    )


def bad_request(exc: InvalidRequestError) -> JSONResponse:
    return error_response(status.HTTP_400_BAD_REQUEST, str(exc), "invalid_request_error")


def describe_errors(errors: Sequence[Any]) -> str:
    """Field paths and messages of validation errors, never the input (it may be PII)."""
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or '(root)'}: {e['msg']}" for e in errors)


async def agent_validation_handler(request: Request, exc: Exception) -> JSONResponse:
    """A body FastAPI rejects on an agent endpoint: audited, 400 OpenAI shape.

    The dashboard endpoints keep FastAPI's 422.
    """
    if not isinstance(exc, RequestValidationError):  # registered for this type only
        raise exc
    if not request.url.path.startswith(_AGENT_PREFIX):
        return await request_validation_exception_handler(request, exc)
    service = get_proxy_service(request)
    try:
        await service.reject_body(describe_errors(exc.errors()))
    except InvalidRequestError as invalid:
        return bad_request(invalid)
