from pydantic import BaseModel

from app.schemas.error_detail import ErrorDetail


class ErrorResponse(BaseModel):
    """Error body in the OpenAI format, so stock clients can parse it."""

    error: ErrorDetail
