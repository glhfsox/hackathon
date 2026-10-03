from fastapi import FastAPI

from app.api import chat, health, tools
from app.core.proxy import ProxyService
from app.protocols.judge import Judge
from app.protocols.policy_provider import PolicyProvider
from app.protocols.upstream import Upstream


def create_app(
    *,
    proxy_service: ProxyService | None = None,
    policy_provider: PolicyProvider | None = None,
    upstream: Upstream | None = None,
    judge: Judge | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Control Layer")
    # Read by app.deps. No concrete implementations exist yet, so the default app has none
    # and the endpoints that need them answer 503 instead of running unchecked.
    app.state.proxy_service = proxy_service
    app.state.policy_provider = policy_provider
    app.state.upstream = upstream
    app.state.judge = judge
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(tools.router)
    return app


app = create_app()
