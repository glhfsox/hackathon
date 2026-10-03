from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.core.proxy import ProxyService
from app.protocols.judge import Judge
from app.protocols.policy_provider import PolicyProvider
from app.protocols.upstream import Upstream


def _configured[T](value: T | None, name: str) -> T:
    if value is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, f"{name} is not configured")
    return value


# Routes depend on the protocols only. The concrete objects are set on app.state by create_app,
# and tests swap them with app.dependency_overrides.
def get_policy_provider(request: Request) -> PolicyProvider:
    provider: PolicyProvider | None = getattr(request.app.state, "policy_provider", None)
    return _configured(provider, "policy provider")


def get_upstream(request: Request) -> Upstream:
    upstream: Upstream | None = getattr(request.app.state, "upstream", None)
    return _configured(upstream, "upstream")


def get_judge(request: Request) -> Judge:
    judge: Judge | None = getattr(request.app.state, "judge", None)
    return _configured(judge, "judge")


def get_proxy_service(request: Request) -> ProxyService:
    service: ProxyService | None = getattr(request.app.state, "proxy_service", None)
    return _configured(service, "proxy service")


PolicyProviderDep = Annotated[PolicyProvider, Depends(get_policy_provider)]
ProxyServiceDep = Annotated[ProxyService, Depends(get_proxy_service)]
UpstreamDep = Annotated[Upstream, Depends(get_upstream)]
JudgeDep = Annotated[Judge, Depends(get_judge)]
