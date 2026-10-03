"""The externally managed attack-signature feed: load, validate and hot-refresh it.

The policy names the feed (`signatures.source`, `signatures.refresh_s`). The feed is data, not
policy: a judge can edit it while the system runs and the next refresh picks it up. A broken
feed never replaces a working one, and a feed that never loaded makes the signatures check fail
closed (docs/architecture.md §4).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, get_args

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.audit import AuditSink
from app.checks.base import Signature
from app.models import Action, AuditRecord
from app.policy import SignatureFeedConfig

logger = logging.getLogger(__name__)

Category = Literal[
    "prompt_injection", "code_execution", "deserialization", "supply_chain", "exfiltration", "other"
]
CATEGORIES: frozenset[str] = frozenset(get_args(Category))

# Default for direct callers; a refresh passes the policy value (signatures.timeout_s).
_HTTP_TIMEOUT_S = 5.0


class SignatureFeedError(Exception):
    """The feed could not be read, parsed or validated. The message says which and why."""


class _Strict(BaseModel):
    # Unknown keys are errors, so a typo such as `case_sensitve` cannot silently change a pattern.
    model_config = ConfigDict(extra="forbid")


class _Entry(_Strict):
    id: str = Field(min_length=1)
    category: Category
    pattern: str = Field(min_length=1)
    description: str
    references: list[str] = Field(default_factory=list)
    case_sensitive: bool = False


class _Document(_Strict):
    version: str
    signatures: list[_Entry]


def _is_url(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def _read(source: str, base_dir: Path | None, timeout_s: float = _HTTP_TIMEOUT_S) -> str:
    if _is_url(source):
        try:
            response = httpx.get(source, timeout=timeout_s)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SignatureFeedError(
                f"signature feed {source} could not be fetched: {exc}"
            ) from exc
        return response.text
    path = Path(source)
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SignatureFeedError(f"signature feed file {path} could not be read: {exc}") from exc


def load_signatures(
    source: str | Path, base_dir: Path | None = None, timeout_s: float = _HTTP_TIMEOUT_S
) -> tuple[list[Signature], str]:
    """Read and validate a feed. Returns (signatures, feed version) or raises SignatureFeedError.

    `source` is an http(s) URL or a file path; a relative path resolves against `base_dir`
    (the policy file's directory), so the policy can say `source: signatures.yaml`.
    """
    source = str(source)
    text = _read(source, base_dir, timeout_s)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SignatureFeedError(f"signature feed {source} is not valid YAML: {exc}") from exc
    try:
        doc = _Document.model_validate(raw)
    except ValidationError as exc:
        raise SignatureFeedError(f"signature feed {source} is invalid: {exc}") from exc

    signatures: list[Signature] = []
    seen: set[str] = set()
    for entry in doc.signatures:
        if entry.id in seen:
            raise SignatureFeedError(f"signature feed {source}: duplicate signature id {entry.id}")
        seen.add(entry.id)
        flags = 0 if entry.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(entry.pattern, flags)
        except re.error as exc:
            raise SignatureFeedError(
                f"signature feed {source}: pattern of {entry.id} does not compile: {exc}"
            ) from exc
        signatures.append(
            Signature(
                id=entry.id,
                pattern=pattern,
                category=entry.category,
                description=entry.description,
            )
        )
    return signatures, doc.version


def _fingerprint(source: str, signatures: list[Signature], version: str) -> tuple:
    return (
        source,
        version,
        tuple(
            (s.id, s.pattern.pattern, s.pattern.flags, s.category, s.description)
            for s in signatures
        ),
    )


class SignatureFeed:
    """The current signature set, refreshed in place.

    Call `refresh` before using `current()` (e.g. once per request); it is a no-op until
    `refresh_s` has passed or the policy points at another source. The clock is injectable so
    tests control time.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._signatures: list[Signature] | None = None
        self.version: str | None = None
        self._source: tuple[str, Path | None] | None = None
        self._last_attempt: float | None = None
        self._fingerprint: tuple | None = None
        self._last_error: str | None = None

    def current(self) -> list[Signature] | None:
        """The last good set, or None while no feed has ever loaded."""
        return self._signatures

    async def refresh(
        self,
        cfg: SignatureFeedConfig,
        base_dir: Path | None,
        audit: AuditSink | None,
        *,
        policy_version: str,
    ) -> None:
        source = (cfg.source, base_dir)
        now = self._clock()
        due = self._last_attempt is None or now - self._last_attempt >= cfg.refresh_s
        if source == self._source and not due:
            return
        # Marked before the load is awaited, so concurrent requests do not start a second load.
        self._source, self._last_attempt = source, now

        try:
            signatures, version = await asyncio.to_thread(
                load_signatures, cfg.source, base_dir, cfg.timeout_s
            )
        except SignatureFeedError as exc:
            await self._failed(str(exc), audit, policy_version)
            return

        recovered = self._last_error is not None
        self._last_error = None
        fingerprint = _fingerprint(cfg.source, signatures, version)
        if fingerprint == self._fingerprint and not recovered:
            return
        self._signatures, self.version, self._fingerprint = signatures, version, fingerprint
        reason = f"signature feed {cfg.source} version {version}: {len(signatures)} signatures"
        logger.info(reason)
        await _audit(audit, "signature_feed_updated", Action.allow, reason, policy_version)

    async def _failed(self, error: str, audit: AuditSink | None, policy_version: str) -> None:
        if self._signatures is None:
            kept = "no signatures loaded yet, the signatures check fails closed"
        else:
            kept = f"keeping version {self.version} ({len(self._signatures)} signatures)"
        logger.error("%s; %s", error, kept)
        # One audit row per distinct failure: a feed that stays broken would otherwise add a row
        # every refresh_s.
        if error == self._last_error:
            return
        self._last_error = error
        await _audit(
            audit, "signature_feed_failed", Action.flag, f"{error}; {kept}", policy_version
        )


async def _audit(
    audit: AuditSink | None, check: str, action: Action, reason: str, policy_version: str
) -> None:
    if audit is None:
        return
    await audit.write(
        AuditRecord(
            ts=datetime.now(UTC).isoformat(),
            check=check,
            action=action,
            reason=reason,
            policy_version=policy_version,
        )
    )
