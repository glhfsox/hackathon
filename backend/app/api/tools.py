from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.errors import NOT_IMPLEMENTED_RESPONSES, not_implemented
from app.schemas.tool_check_request import ToolCheckRequest
from app.schemas.tool_check_response import ToolCheckResponse

router = APIRouter(prefix="/v1", tags=["tool guard"])


@router.post(
    "/tools/check",
    response_model=ToolCheckResponse,
    responses=NOT_IMPLEMENTED_RESPONSES,
)
async def check_tool(body: ToolCheckRequest) -> JSONResponse:
    # Stub until the pipeline exists. It will run only the tool_call checkpoint.
    return not_implemented()
