from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse

from app.schemas.error_detail import ErrorDetail
from app.schemas.error_response import ErrorResponse

NOT_IMPLEMENTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_501_NOT_IMPLEMENTED: {"model": ErrorResponse}
}


def error_response(status_code: int, message: str, error_type: str) -> JSONResponse:
    """An error in the OpenAI shape, so stock clients can parse it."""
    error = ErrorResponse(error=ErrorDetail(message=message, type=error_type))
    return JSONResponse(error.model_dump(), status_code=status_code)


def not_implemented() -> JSONResponse:
    """501 for endpoints whose logic is not built yet."""
    return error_response(status.HTTP_501_NOT_IMPLEMENTED, "not implemented", "not_implemented")
