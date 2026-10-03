from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.error_detail import ErrorDetail
from app.schemas.error_response import ErrorResponse

router = APIRouter(prefix="/v1", tags=["proxy"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    responses={status.HTTP_501_NOT_IMPLEMENTED: {"model": ErrorResponse}},
)
async def chat_completions(body: ChatCompletionRequest) -> JSONResponse:
    # Stub until the adapter, the pipeline and the upstream client exist
    error = ErrorResponse(error=ErrorDetail(message="not implemented", type="not_implemented"))
    return JSONResponse(error.model_dump(), status_code=status.HTTP_501_NOT_IMPLEMENTED)
