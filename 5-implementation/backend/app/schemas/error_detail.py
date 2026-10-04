from pydantic import BaseModel


class ErrorDetail(BaseModel):
    message: str
    type: str  # e.g. invalid_request_error, authentication_error, not_implemented
