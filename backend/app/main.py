import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.adapters.openai import OpenAIAdapter
from app.api import audit, chat, health, metrics, policy
from app.api.errors import agent_validation_handler
from app.core.audit_reader import JsonlAuditReader
from app.core.budget import UsageLedger
from app.core.jev import JevClient
from app.core.pipeline import Pipeline
from app.core.policy_store import PolicyStore
from app.core.proxy import ProxyService
from app.core.signatures import SignatureFeed
from app.core.upstream import HttpUpstream
from app.observability.sinks import FanoutAuditSink, JsonlAuditSink
from app.observability.stats import StatsExporter
from app.observability.tracing import build_langfuse_sink
from app.protocols.audit import AuditReader, AuditSink
from app.protocols.judge import Judge
from app.protocols.policy_provider import PolicyProvider
from app.protocols.upstream import Upstream

BACKEND_DIR = Path(__file__).resolve().parents[1]
# The Vite dev server of the dashboard (docs/architecture.md §2).
FRONTEND_ORIGINS = ["http://localhost:5173"]


def _real_app(
    policy_path: Path | None, logs_dir: Path | None
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """The lifespan that builds the real application on startup and tears it down on shutdown.

    Paths come from the arguments, else the env (POLICY_PATH, LOGS_DIR), else the files next to
    the backend. Nothing is read before startup, so importing app.main has no side effects.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        path = policy_path or Path(os.environ.get("POLICY_PATH") or BACKEND_DIR / "policy.yaml")
        logs = logs_dir or Path(os.environ.get("LOGS_DIR") or BACKEND_DIR / "logs")
        langfuse = build_langfuse_sink()
        # One client for the upstream and Jev calls; each call passes its policy timeout.
        async with httpx.AsyncClient() as http:
            try:
                sinks: list[AuditSink] = [JsonlAuditSink(logs)]
                if langfuse is not None:
                    sinks.append(langfuse)
                audit_sink = FanoutAuditSink(sinks)
                store = PolicyStore(path, audit_sink)
                # An invalid policy at startup raises: the layer does not run without a policy.
                await store.load_initial()
                feed = SignatureFeed()
                ledger = UsageLedger()
                # One long-lived client, so health() state and the verdict cache survive
                # requests; it reads the jev section in force on every call.
                jev = JevClient(lambda: store.current().policy.jev, http)
                upstream = HttpUpstream(http)
                pipeline = Pipeline(
                    audit=audit_sink, ledger=ledger, signatures=feed.current, judge=jev
                )
                app.state.policy_provider = store
                app.state.upstream = upstream
                app.state.judge = jev
                app.state.audit_reader = JsonlAuditReader(logs)
                app.state.proxy_service = ProxyService(
                    OpenAIAdapter(), pipeline, upstream, store, ledger, audit_sink, feed
                )
                exporter = StatsExporter(logs, lambda: store.current().policy)
                store.start()
                exporter.start()
                try:
                    yield
                finally:
                    await exporter.stop()
                    await store.stop()
            finally:
                if langfuse is not None:
                    # Sends what is still queued; blocks on the network.
                    await asyncio.to_thread(langfuse.shutdown)

    return lifespan


def create_app(
    *,
    proxy_service: ProxyService | None = None,
    policy_provider: PolicyProvider | None = None,
    upstream: Upstream | None = None,
    judge: Judge | None = None,
    audit_reader: AuditReader | None = None,
    policy_path: Path | None = None,
    logs_dir: Path | None = None,
) -> FastAPI:
    """The application. Given any of the objects, it uses only those (tests); given none, it
    builds the real ones on startup from `policy_path` and `logs_dir` (see _real_app)."""
    given = (proxy_service, policy_provider, upstream, judge, audit_reader)
    lifespan = _real_app(policy_path, logs_dir) if all(o is None for o in given) else None
    app = FastAPI(title="AI Control Layer", lifespan=lifespan)
    # Read by app.deps. Endpoints whose objects are missing answer 503 instead of running
    # unchecked.
    app.state.proxy_service = proxy_service
    app.state.policy_provider = policy_provider
    app.state.upstream = upstream
    app.state.judge = judge
    app.state.audit_reader = audit_reader
    app.add_middleware(
        CORSMiddleware, allow_origins=FRONTEND_ORIGINS, allow_methods=["*"], allow_headers=["*"]
    )
    # A body FastAPI rejects on /v1 is audited and answered 400 in the OpenAI shape.
    app.add_exception_handler(RequestValidationError, agent_validation_handler)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(policy.router)
    app.include_router(audit.router)
    app.include_router(metrics.router)
    return app


app = create_app()
