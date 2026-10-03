"""Langfuse tracing as an audit sink: every AuditRecord becomes an observation in Langfuse.

Privacy: only the fields of the AuditRecord are sent. The record has no field for message
contents, tool arguments or tool results, so those never reach Langfuse, and the checks write
their reasons without the matched secret or PII. Trace input/output are never set.

Mapping (Langfuse Python SDK 4.16.0, see backend/docs/observability.md):
- One trace per request_id. Its id is `Langfuse.create_trace_id(seed=request_id)`, so the rows of
  all four checkpoints of a request land in the same trace without any shared state here.
  Trace attributes (user_id = caller_id, metadata model and policy_version, tags) are set with
  `propagate_attributes`, the SDK 4 way to set them.
- One `guardrail` observation per check row, named `<checkpoint>/<check>`, with the whole record
  as metadata, level ERROR for block and WARNING for flag, and the policy version as version.
- A Jev row with a real verdict (decided by Jev or the fallback) adds a NUMERIC `jev_risk` score.
  A blocking row adds a CATEGORICAL `blocked_by` score (the check id) and the tags
  `block` and `blocked_by:<check>`.
- System events become events: inside the request's trace when they have a request_id, else a
  standalone trace named after the event (policy_loaded, signature_feed_failed, ...).
- A `turn_summary` row (one per checkpoint) is such an event in its request's trace, named
  `<checkpoint>/turn_summary`, with the agent and economic fields in its metadata. It adds no
  tags: it is on every request, so tagging it would mark every trace a system event.

Timing: in SDK 4.16.0 `start_observation` has no start time parameter (it starts "now") but
`end()` accepts an explicit end time. An observation therefore starts when the record reaches
this sink, which the pipeline does right after the check finished, and lasts the check's own
`latency_ms`. The exact `ts` and `latency_ms` are in the metadata.

Nothing here waits on the network: the SDK creates OpenTelemetry spans and queues scores, and its
background threads export them in batches. Call `flush()`/`shutdown()` when the app stops.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from langfuse import Langfuse, propagate_attributes

from app.core.metrics import AI_CHECK, SYSTEM_EVENTS
from app.models import TURN_SUMMARY, Action, AuditRecord, DecidedBy

log = logging.getLogger(__name__)

TRACE_NAME = "control-layer"
JEV_SCORE = "jev_risk"
BLOCK_SCORE = "blocked_by"
SYSTEM_EVENT_TAG = "system_event"
# The SDK's own default host, used when neither LANGFUSE_HOST nor LANGFUSE_BASE_URL is set.
LANGFUSE_CLOUD_URL = "https://cloud.langfuse.com"

_LEVEL = {Action.BLOCK: "ERROR", Action.FLAG: "WARNING"}


def _tags(record: AuditRecord, system_event: bool) -> list[str]:
    if record.check == TURN_SUMMARY:
        # The blocking check row already tags the trace.
        return []
    tags = [SYSTEM_EVENT_TAG] if system_event else []
    if record.action != Action.ALLOW:
        tags.append(record.action.value)
    if record.action == Action.BLOCK and not system_event:
        tags.append(f"{BLOCK_SCORE}:{record.check}")
    return tags


class LangfuseAuditSink:
    """Sends every audit record to Langfuse. `client` is a `langfuse.Langfuse` (a fake in tests).

    `propagate` is `langfuse.propagate_attributes` (module-level in the SDK, so it is injected
    separately; tests pass a recorder).
    """

    def __init__(
        self,
        client: Langfuse,
        *,
        propagate: Callable[..., AbstractContextManager[Any]] = propagate_attributes,
    ) -> None:
        self._client = client
        self._propagate = propagate

    async def write(self, record: AuditRecord) -> None:
        system_event = record.check in SYSTEM_EVENTS or record.checkpoint is None
        trace_id = Langfuse.create_trace_id(seed=record.request_id) if record.request_id else None
        trace_context = {"trace_id": trace_id} if trace_id else None
        name = f"{record.checkpoint.value}/{record.check}" if record.checkpoint else record.check
        trace_metadata = {"model": record.model, "policy_version": record.policy_version}
        common: dict[str, Any] = {
            "trace_context": trace_context,
            "name": name,
            "output": {"action": record.action.value, "reason": record.reason},
            "metadata": record.model_dump(mode="json"),
            "version": record.policy_version,
            "level": _LEVEL.get(record.action, "DEFAULT"),
            "status_message": record.reason if record.action in _LEVEL else None,
        }
        tags = _tags(record, system_event)

        with self._propagate(
            user_id=record.caller_id,
            # A standalone system event is its own trace: name it after the event.
            trace_name=TRACE_NAME if trace_id else record.check,
            metadata={k: v for k, v in trace_metadata.items() if v is not None},
            tags=tags or None,
        ):
            if system_event:
                self._client.create_event(**common)
                return
            observation = self._client.start_observation(as_type="guardrail", **common)
        observation.end(end_time=time.time_ns() + int(record.latency_ms * 1_000_000))

        if record.check == AI_CHECK and record.decided_by != DecidedBy.RULES:
            # decided_by rules means Jev gave no verdict (unavailable, nothing to judge): no risk.
            self._client.create_score(
                name=JEV_SCORE,
                value=record.score,
                trace_id=observation.trace_id,
                observation_id=observation.id,
                data_type="NUMERIC",
                comment=f"{name} ({record.decided_by.value}): {record.reason}",
            )
        if record.action == Action.BLOCK:
            self._client.create_score(
                name=BLOCK_SCORE,
                value=record.check,
                trace_id=observation.trace_id,
                data_type="CATEGORICAL",
                comment=f"{name}: {record.reason}",
            )

    def flush(self) -> None:
        """Send everything queued. Blocks on the network: in async code use asyncio.to_thread."""
        self._client.flush()

    def shutdown(self) -> None:
        """Flush and stop the SDK's background threads. Blocks like flush()."""
        self._client.shutdown()


def build_langfuse_sink() -> LangfuseAuditSink | None:
    """A sink for the Langfuse named by the environment, or None when tracing is not configured.

    Enabled only when LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set and non-empty.
    The host is LANGFUSE_HOST (Langfuse Cloud or a self-hosted URL); the SDK's newer name
    LANGFUSE_BASE_URL wins when both are set. Without keys no client is created, so nothing
    touches the network.
    """
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    if not public_key or not secret_key:
        log.info("Langfuse tracing off: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set")
        return None
    # Resolved here, not left to the SDK: an empty LANGFUSE_HOST (as .env.example leaves it)
    # would otherwise become an empty base URL.
    base_url = (
        os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or LANGFUSE_CLOUD_URL
    )
    log.info("Langfuse tracing on, sending to %s", base_url)
    return LangfuseAuditSink(
        Langfuse(public_key=public_key, secret_key=secret_key, base_url=base_url)
    )
