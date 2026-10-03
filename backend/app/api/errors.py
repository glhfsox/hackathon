from typing import Any

from fastapi import status
from fastapi.responses import JSONResponse

from app.schemas.error_detail import ErrorDetail
from app.schemas.error_response import ErrorResponse

NOT_IMPLEMENTED_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_501_NOT_IMPLEMENTED: {"model": ErrorResponse}
}


def not_implemented() -> JSONResponse:
    """501 in the OpenAI error shape, for endpoints whose logic is not built yet."""
    error = ErrorResponse(error=ErrorDetail(message="not implemented", type="not_implemented"))
    return JSONResponse(error.model_dump(), status_code=status.HTTP_501_NOT_IMPLEMENTED)
