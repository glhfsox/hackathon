"""Loads the policy file, validates it, hot-reloads it and swaps it in atomically.

docs/architecture.md §6. The file is the single source: API edits are written to it, and the poll
loop picks up edits made in an editor. An invalid policy never replaces the one in force.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from app.models import Action, AuditRecord, Checkpoint, Mode, PolicyError, PolicySnapshot
from app.models.policy import CHECK_SPECS, Policy
from app.protocols.audit import AuditSink
from app.protocols.policy_provider import PolicyRejectedError

log = logging.getLogger(__name__)

# policy_version on an audit record written while no policy has ever been in force.
_NO_POLICY = "none"


def _format_errors(errors: list[PolicyError]) -> str:
    return "; ".join(f"{e.loc or '(root)'}: {e.msg}" for e in errors)


def _dotted(loc: tuple[int | str, ...]) -> str:
    parts = [str(p) for p in loc]
    # CheckConfig regroups a check's checkpoint keys under `modes` internally. That key is not in
    # the file, so drop it and point the operator at the key they actually wrote.
    if len(parts) > 2 and parts[0] == "checks" and parts[2] == "modes":
        del parts[2]
    return ".".join(parts)


def _yaml_msg(exc: yaml.YAMLError) -> str:
    if isinstance(exc, yaml.MarkedYAMLError) and exc.problem_mark is not None:
        mark = exc.problem_mark
        return f"YAML syntax error at line {mark.line + 1}, column {mark.column + 1}: {exc.problem}"
    return f"YAML syntax error: {exc}"


class _DuplicateKeys(yaml.YAMLError):
    def __init__(self, errors: list[PolicyError]) -> None:
        super().__init__(_format_errors(errors))
        self.errors = errors


def _find_duplicates(
    node: yaml.Node, path: tuple[str, ...], errors: list[PolicyError], walked: set[int]
) -> None:
    # An alias points at a node already walked; skipping it also ends a recursive alias.
    if id(node) in walked:
        return
    walked.add(id(node))
    if isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            _find_duplicates(item, (*path, str(i)), errors, walked)
        return
    if not isinstance(node, yaml.MappingNode):
        return
    first_line: dict[tuple[str, str], int] = {}
    for key, value in node.value:
        child = path
        if isinstance(key, yaml.ScalarNode):
            child = (*path, key.value)
            line = key.start_mark.line + 1
            # The resolved tag keeps `1` and `"1"` apart, as they are different keys once loaded.
            ident = (key.tag, key.value)
            if ident in first_line:
                errors.append(
                    PolicyError(
                        loc=".".join(child),
                        msg=f"duplicate key '{key.value}' at line {line} (first at line "
                        f"{first_line[ident]}): YAML would silently keep only the last one; "
                        "write each key once",
                    )
                )
            else:
                first_line[ident] = line
        _find_duplicates(value, child, errors, walked)


class _PolicyLoader(yaml.SafeLoader):
    """SafeLoader for a file people edit by hand while it is in force.

    A key written twice in one mapping is an error: YAML keeps the last one silently, so a second
    `tool_args:` section or a stray `input: off` would quietly remove protection. And only
    true/false are booleans: YAML 1.1 also reads on/off/yes/no as booleans, which made the
    documented mode `off` invalid unless quoted.
    """

    def construct_document(self, node: yaml.Node) -> Any:
        errors: list[PolicyError] = []
        _find_duplicates(node, (), errors, set())
        if errors:
            raise _DuplicateKeys(errors)
        return super().construct_document(node)


_BOOL_TAG = "tag:yaml.org,2002:bool"
# Own copy of the resolver table, so yaml.safe_load elsewhere keeps standard YAML 1.1.
_PolicyLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_PolicyLoader.add_implicit_resolver(
    _BOOL_TAG, re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def parse_policy(yaml_text: str) -> Policy:
    """Parse and validate policy YAML. Every failure raises PolicyRejectedError."""
    try:
        raw = yaml.load(yaml_text, Loader=_PolicyLoader)  # a SafeLoader: builds plain data only
    except _DuplicateKeys as exc:
        raise PolicyRejectedError(exc.errors) from exc
    except yaml.YAMLError as exc:
        raise PolicyRejectedError([PolicyError(loc="", msg=_yaml_msg(exc))]) from exc
    if not isinstance(raw, dict):
        got = "an empty document" if raw is None else f"a {type(raw).__name__}"
        raise PolicyRejectedError(
            [PolicyError(loc="", msg=f"the policy must be a YAML mapping, got {got}")]
        )
    try:
        return Policy.model_validate(raw)
    except ValidationError as exc:
        raise PolicyRejectedError(
            [PolicyError(loc=_dotted(e["loc"]), msg=e["msg"]) for e in exc.errors()]
        ) from exc


def _load(yaml_text: str, path: Path) -> PolicySnapshot:
    policy = parse_policy(yaml_text)
    # The content hash makes two different files never share a version, even when the operator
    # forgets to bump `version`.
    digest = hashlib.sha256(yaml_text.encode("utf-8")).hexdigest()[:8]
    return PolicySnapshot(
        policy=policy,
        version=f"{policy.version}+{digest}",
        yaml=yaml_text,
        loaded_at=datetime.now(UTC),
        path=path,
    )


def _enabled_summary(policy: Policy) -> str:
    """How many checks run under the active profile, naming those off at every checkpoint. A
    policy that turns protection off is valid (the policy decides), but never silently so."""
    off = [cid for cid in CHECK_SPECS if all(policy.mode(cid, cp) == Mode.OFF for cp in Checkpoint)]
    summary = f"{len(CHECK_SPECS) - len(off)} checks on under profile {policy.active_profile}"
    return f"{summary}; off: {', '.join(off)}" if off else summary


def _atomic_write(path: Path, text: str) -> None:
    """Write through a temp file in the same directory and os.replace it over the target, so a
    reader (the poll loop, an editor) sees the old file or the new one, never half of one."""
    data = text.encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        # After a successful replace the temp name is gone; after a failure this cleans it up.
        tmp.unlink(missing_ok=True)


class PolicyStore:
    """Holds the policy in force and keeps it in step with the file.

    Reading the file, validating it and swapping the snapshot happen with no await in between, so
    on the event loop each reload is atomic with respect to requests and to replace(). The swap is
    one attribute assignment: a request that already took current() keeps its snapshot.
    """

    def __init__(self, path: Path, audit: AuditSink) -> None:
        self._path = path
        self._audit = audit
        self._current: PolicySnapshot | None = None
        # (mtime_ns, size) at the last look; None while the file cannot be stat'ed.
        self._seen: tuple[int, int] | None = None
        # The last broken file content the poll loop audited, so one broken save is audited once.
        self._rejected: str | None = None
        self._task: asyncio.Task[None] | None = None

    async def load_initial(self) -> PolicySnapshot:
        """Load the file at startup. An invalid first file raises PolicyRejectedError."""
        self._seen = self._stat()
        try:
            loaded = _load(self._read(), self._path)
        except PolicyRejectedError as exc:
            await self._audit_rejected(exc.errors, "startup")
            raise
        self._current = loaded
        await self._audit_loaded(loaded, "startup")
        return loaded

    def current(self) -> PolicySnapshot:
        if self._current is None:
            raise RuntimeError("no policy loaded; call load_initial() first")
        return self._current

    def validate(self, yaml_text: str) -> list[PolicyError]:
        """Field-level errors, empty when valid. No side effects."""
        try:
            parse_policy(yaml_text)
        except PolicyRejectedError as exc:
            return exc.errors
        return []

    async def save(self, yaml_text: str) -> PolicySnapshot:
        """Validate, write the file atomically, then swap.

        An invalid policy raises PolicyRejectedError and leaves the file untouched.
        """
        try:
            loaded = _load(yaml_text, self._path)
        except PolicyRejectedError as exc:
            await self._audit_rejected(exc.errors, "api")
            raise
        # The poll loop will see the new mtime, find the text equal to current() and do nothing.
        _atomic_write(self._path, yaml_text)
        self._rejected = None
        self._current = loaded
        await self._audit_loaded(loaded, "api")
        return loaded

    async def poll_once(self) -> bool:
        """Reload if the file changed since the last look. True when a new policy was swapped in."""
        seen = self._stat()
        if seen == self._seen:
            return False
        self._seen = seen
        try:
            text = self._read()
        except PolicyRejectedError as exc:
            # Missing or unreadable, e.g. mid-save in an editor that deletes and recreates the
            # file: a rejected edit, the old policy stays.
            await self._reject_file(exc.errors, exc.errors[0].msg)
            return False
        if text == self.current().yaml:
            # Saved unchanged, or reverted to the policy in force after a broken edit.
            self._rejected = None
            return False
        try:
            loaded = _load(text, self._path)
        except PolicyRejectedError as exc:
            await self._reject_file(exc.errors, text)
            return False
        self._rejected = None
        self._current = loaded
        await self._audit_loaded(loaded, "file")
        return True

    def start(self, interval_s: float = 1.0) -> None:
        if self._task is not None:
            raise RuntimeError("policy poll loop already started")
        self._task = asyncio.create_task(self._poll_loop(interval_s), name="policy-poll")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _poll_loop(self, interval_s: float) -> None:
        while True:
            await asyncio.sleep(interval_s)
            try:
                await self.poll_once()
            except Exception:
                # One failure (e.g. the audit sink is down) must not end hot reload for the rest
                # of the run.
                log.exception("policy poll of %s failed", self._path)

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            # Missing mid-save; _read() then reports the actual error.
            return None
        return st.st_mtime_ns, st.st_size

    def _read(self) -> str:
        try:
            # Bytes, not read_text(): no newline translation, so the version hash is the same
            # whether the text arrived through replace() or through the file.
            return self._path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise PolicyRejectedError(
                [PolicyError(loc="", msg=f"cannot read {self._path}: {exc}")]
            ) from exc

    async def _reject_file(self, errors: list[PolicyError], content_key: str) -> None:
        if content_key == self._rejected:
            return
        self._rejected = content_key
        await self._audit_rejected(errors, "file")

    async def _audit_loaded(self, loaded: PolicySnapshot, source: str) -> None:
        reason = f"policy {loaded.version} loaded ({source}); {_enabled_summary(loaded.policy)}"
        log.info("%s from %s", reason, self._path)
        await self._audit.write(
            AuditRecord(
                ts=loaded.loaded_at.isoformat(),
                check="policy_loaded",
                action=Action.ALLOW,
                reason=reason,
                policy_version=loaded.version,
            )
        )

    async def _audit_rejected(self, errors: list[PolicyError], source: str) -> None:
        version = self._current.version if self._current is not None else _NO_POLICY
        kept = f"{version} stays active" if self._current is not None else "no policy active"
        reason = f"policy rejected ({source}), {kept}: {_format_errors(errors)}"
        log.warning("%s", reason)
        await self._audit.write(
            AuditRecord(
                ts=datetime.now(UTC).isoformat(),
                check="policy_rejected",
                action=Action.BLOCK,
                reason=reason,
                policy_version=version,
            )
        )
