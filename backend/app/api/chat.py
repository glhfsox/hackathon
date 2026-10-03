from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.errors import NOT_IMPLEMENTED_RESPONSES, not_implemented
from app.schemas.chat_completion_request import ChatCompletionRequest
from app.schemas.chat_completion_response import ChatCompletionResponse

router = APIRouter(prefix="/v1", tags=["proxy"])


@router.post(
    "/chat/completions",
    response_model=ChatCompletionResponse,
    responses=NOT_IMPLEMENTED_RESPONSES,
)
async def chat_completions(body: ChatCompletionRequest) -> JSONResponse:
    # Stub until the adapter, the pipeline and the upstream client exist
    return not_implemented()
