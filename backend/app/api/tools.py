from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.api.errors import AGENT_ERROR_RESPONSES, unauthorized
from app.core.proxy import UnauthorizedError
from app.deps import ApiKeyDep, ProxyServiceDep
from app.schemas.tool_check_request import ToolCheckRequest
from app.schemas.tool_check_response import ToolCheckResponse

router = APIRouter(prefix="/v1", tags=["tool guard"])


@router.post("/tools/check", response_model=ToolCheckResponse, responses=AGENT_ERROR_RESPONSES)
async def check_tool(
    body: ToolCheckRequest, service: ProxyServiceDep, api_key: ApiKeyDep
) -> ToolCheckResponse | JSONResponse:
    """Runs only the tool_call checkpoint. A malformed body is answered by the app's handler."""
    try:
        return await service.check_tool(body, api_key=api_key)
    except UnauthorizedError as exc:
        return unauthorized(exc)
