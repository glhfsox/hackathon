from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.errors import AGENT_ERROR_RESPONSES, bad_request
from app.deps import ProxyServiceDep
from app.protocols.adapter import InvalidRequestError
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse

router = APIRouter(prefix="/v1", tags=["proxy"])


@router.post(
    "/chat/completions", response_model=ChatCompletionResponse, responses=AGENT_ERROR_RESPONSES
)
async def chat_completions(
    body: ChatCompletionRequest, service: ProxyServiceDep
) -> ChatCompletionResponse | JSONResponse:
    try:
        return await service.handle(body)
    except InvalidRequestError as exc:
        return bad_request(exc)
