from fastapi import APIRouter

from app.deps import JudgeHealthDep, PolicyProviderDep
from app.schemas.health_response import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(policy: PolicyProviderDep, judge: JudgeHealthDep) -> HealthResponse:
    return HealthResponse.model_validate(
        {"status": "ok", "policy_version": policy.current().version, **(await judge.health())}
    )
