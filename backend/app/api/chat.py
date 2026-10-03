from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.api.errors import error_response
from app.deps import ProxyServiceDep
from app.protocols.adapter import InvalidRequestError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse
from app.schemas.error_response import ErrorResponse

router = APIRouter(prefix="/v1", tags=["proxy"])

# Placeholder until auth exists: the policy will map the Bearer key to a caller
UNAUTHENTICATED_CALLER = "unauthenticated"

_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse}
}


@router.post("/chat/completions", response_model=ChatCompletionResponse, responses=_RESPONSES)
async def chat_completions(
    body: ChatCompletionRequest, service: ProxyServiceDep
) -> ChatCompletionResponse | JSONResponse:
    try:
        return await service.handle(body, caller_id=UNAUTHENTICATED_CALLER)
    except InvalidRequestError as exc:
        return error_response(status.HTTP_400_BAD_REQUEST, str(exc), "invalid_request_error")
