from fastapi import APIRouter

from app.schemas.health_response import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    # Static until the policy provider and the Jev client are wired in
    return HealthResponse(status="ok", policy_version=None, jev="down", fallback="down")
