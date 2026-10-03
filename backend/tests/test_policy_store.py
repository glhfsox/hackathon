"""PolicyStore: load, hot reload, rejection of invalid edits, atomic API replace (spec US2)."""

from __future__ import annotations

import asyncio
import copy
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.policy_store import PolicyStore
from app.models import Action, AuditRecord
from app.observability.sinks import MemoryAuditSink
from app.protocols.policy_provider import PolicyRejectedError

DEV_POLICY = yaml.safe_load((Path(__file__).parent.parent / "policy.yaml").read_text())


def _policy_yaml(mutate: Callable[[dict[str, Any]], None] | None = None) -> str:
    raw = copy.deepcopy(DEV_POLICY)
    if mutate is not None:
        mutate(raw)
    return yaml.safe_dump(raw, sort_keys=False)


def _write(path: Path, text: str) -> None:
    """Save like an editor would. Bumps mtime if the filesystem clock did not move between two
    quick writes, which a human save seconds apart always does."""
    before = path.stat().st_mtime_ns if path.exists() else None
    path.write_text(text)
    st = path.stat()
    if st.st_mtime_ns == before:
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))


def _mentions(errors: list[dict[str, str]], needle: str) -> bool:
    """The error points at `needle` by loc or, for cross-field errors, by message."""
    return any(e.loc.startswith(needle) or needle in e.msg for e in errors)


def _set(path: list[str], value: Any) -> Callable[[dict[str, Any]], None]:
    def mutate(raw: dict[str, Any]) -> None:
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[path[-1]] = value

    return mutate


def _drop(path: list[str]) -> Callable[[dict[str, Any]], None]:
    def mutate(raw: dict[str, Any]) -> None:
        node = raw
        for key in path[:-1]:
            node = node[key]
        del node[path[-1]]

    return mutate


def _rename(path: list[str], new: str) -> Callable[[dict[str, Any]], None]:
    """A typo in an existing key: the section keeps its content under the misspelled name."""

    def mutate(raw: dict[str, Any]) -> None:
        node = raw
        for key in path[:-1]:
            node = node[key]
        node[new] = node.pop(path[-1])

    return mutate


# A valid edit: a stricter Jev threshold.
VALID_STRICT = _policy_yaml(_set(["jev_threshold"], 0.4))

# (id, broken policy text, what the field-level error must point at)
INVALID_EDITS = [
    ("unknown_key", _policy_yaml(_set(["bogus"], 1)), "bogus"),
    # Modes and profiles were removed: an old policy must not load silently.
    (
        "old_mode_key",
        _policy_yaml(_set(["checks", "pii_secrets", "tool_result"], "redact")),
        "checks.pii_secrets.tool_result",
    ),
    (
        "old_profiles",
        _policy_yaml(_set(["profiles"], {"strict": {"jev_threshold": 0.4}})),
        "profiles",
    ),
    (
        "unknown_model",
        _policy_yaml(_set(["checks", "permissions", "allowed_models"], ["gpt-9"])),
        "checks.permissions.allowed_models",
    ),
    # The callers section was removed: an old policy that still has it must not load silently.
    ("callers_section", _policy_yaml(_set(["callers"], {"demo": {"role": "dev"}})), "callers"),
    (
        "negative_budget",
        _policy_yaml(_set(["checks", "budget", "tokens_per_day"], -1)),
        "checks.budget.tokens_per_day",
    ),
    ("bad_threshold", _policy_yaml(_set(["jev_threshold"], 1.5)), "jev_threshold"),
    ("jev_latest", _policy_yaml(_set(["jev", "model"], "jev-latest")), "jev.model"),
    (
        "typo_check_id",
        _policy_yaml(_rename(["checks", "pii_secrets"], "pii_secret")),
        "checks.pii_secret",
    ),
    (
        "typo_check_param",
        _policy_yaml(_rename(["checks", "loop_detection", "max_tool_calls"], "max_tool_cals")),
        "checks.loop_detection.max_tool_cals",
    ),
    (
        "bad_param_type",
        _policy_yaml(_set(["checks", "jev", "max_chars"], "lots")),
        "checks.jev.max_chars",
    ),
    ("pii_off_under_jev", _policy_yaml(_drop(["checks", "pii_secrets"])), "checks.pii_secrets"),
    ("yaml_syntax", "version: '0.1\njev_threshold: [0.6\n", "YAML syntax error"),
    ("empty_file", "", "empty"),
    # YAML would keep the last one silently and change the threshold.
    ("duplicate_key", _policy_yaml() + "jev_threshold: 0.9\n", "jev_threshold"),
]


@pytest.fixture
def audit() -> MemoryAuditSink:
    return MemoryAuditSink()


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    path.write_text(_policy_yaml())
    return path


@pytest.fixture
async def store(policy_path: Path, audit: MemoryAuditSink) -> PolicyStore:
    store = PolicyStore(policy_path, audit)
    await store.load_initial()
    return store


def _last(audit: MemoryAuditSink) -> AuditRecord:
    return audit.records[-1]


async def test_initial_load(policy_path: Path, audit: MemoryAuditSink) -> None:
    store = PolicyStore(policy_path, audit)
    loaded = await store.load_initial()

    assert store.current() is loaded
    assert loaded.policy.jev_threshold == 0.6
    assert loaded.path == policy_path
    assert loaded.yaml == policy_path.read_text()
    prefix, digest = loaded.version.split("+")
    assert prefix == "0.2" and len(digest) == 8
    assert loaded.loaded_at.utcoffset().total_seconds() == 0
    assert len(audit.records) == 1
    rec = _last(audit)
    assert (rec.check, rec.action, rec.policy_version) == (
        "policy_loaded",
        Action.ALLOW,
        loaded.version,
    )
    assert loaded.version in rec.reason
    assert "7 checks on, jev_threshold 0.6" in rec.reason
    assert "off:" not in rec.reason


def _without(*check_ids: str) -> Callable[[dict[str, Any]], None]:
    def mutate(raw: dict[str, Any]) -> None:
        for cid in check_ids:
            del raw["checks"][cid]

    return mutate


# An accepted policy that turns protection off must say so in its audit row.
@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (
            _set(["checks"], {}),
            "0 checks on, jev_threshold 0.6; 6 roles, 12 permissions; "
            "off: permissions, budget, loop_detection, signatures, tool_args, pii_secrets, jev",
        ),
        (
            _without("signatures", "jev"),
            "5 checks on, jev_threshold 0.6; 6 roles, 12 permissions; off: signatures, jev",
        ),
    ],
    ids=["empty_checks", "two_off"],
)
async def test_loaded_audit_row_names_checks_that_are_off(
    store: PolicyStore,
    audit: MemoryAuditSink,
    mutate: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    loaded = await store.save(_policy_yaml(mutate))

    rec = _last(audit)
    assert (rec.check, rec.action) == ("policy_loaded", Action.ALLOW)
    assert rec.reason == f"policy {loaded.version} loaded (api); {expected}"


@pytest.mark.parametrize("text", ["version: [", "", "version: '0.1'\n"])
async def test_invalid_initial_load_raises(
    policy_path: Path, audit: MemoryAuditSink, text: str
) -> None:
    policy_path.write_text(text)
    store = PolicyStore(policy_path, audit)
    with pytest.raises(PolicyRejectedError) as exc:
        await store.load_initial()
    assert exc.value.errors
    assert _last(audit).check == "policy_rejected"
    with pytest.raises(RuntimeError):
        store.current()


async def test_missing_initial_file_raises(tmp_path: Path, audit: MemoryAuditSink) -> None:
    with pytest.raises(PolicyRejectedError, match="cannot read"):
        await PolicyStore(tmp_path / "absent.yaml", audit).load_initial()


async def test_valid_edit_picked_up_by_poll(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    old = store.current()
    _write(policy_path, VALID_STRICT)

    assert await store.poll_once() is True
    new = store.current()
    assert new.policy.jev_threshold == 0.4
    assert new.version != old.version
    rec = _last(audit)
    assert (rec.check, rec.action, rec.policy_version) == (
        "policy_loaded",
        Action.ALLOW,
        new.version,
    )
    # Nothing changed since: no reload, no audit.
    count = len(audit.records)
    assert await store.poll_once() is False
    assert len(audit.records) == count


@pytest.mark.parametrize(
    ("text", "points_at"), [c[1:] for c in INVALID_EDITS], ids=[c[0] for c in INVALID_EDITS]
)
async def test_invalid_edit_rejected_old_policy_kept(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink, text: str, points_at: str
) -> None:
    old = store.current()
    errors = store.validate(text)
    assert errors and all(set(e.model_dump()) == {"loc", "msg"} for e in errors)
    assert _mentions(errors, points_at), errors

    _write(policy_path, text)
    assert await store.poll_once() is False

    assert store.current() is old
    rec = _last(audit)
    assert (rec.check, rec.action, rec.policy_version) == (
        "policy_rejected",
        Action.BLOCK,
        old.version,
    )
    assert points_at in rec.reason


async def test_same_broken_content_not_reaudited(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    broken = _policy_yaml(_set(["bogus"], 1))
    _write(policy_path, broken)
    await store.poll_once()
    count = len(audit.records)

    await store.poll_once()  # unchanged file
    _write(policy_path, broken)  # saved again with the same content
    await store.poll_once()
    assert len(audit.records) == count

    _write(policy_path, _policy_yaml(_set(["other_bogus"], 1)))  # a different broken edit
    await store.poll_once()
    assert len(audit.records) == count + 1


async def test_fix_after_broken_edit_recovers(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    broken = _policy_yaml(_set(["jev", "model"], "jev-latest"))
    _write(policy_path, broken)
    await store.poll_once()

    _write(policy_path, VALID_STRICT)
    assert await store.poll_once() is True
    assert store.current().policy.jev_threshold == 0.4
    assert _last(audit).check == "policy_loaded"

    # The same mistake made again later is a new event and is audited again.
    _write(policy_path, broken)
    await store.poll_once()
    assert _last(audit).check == "policy_rejected"


async def test_half_written_or_missing_file_keeps_old_policy(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    old = store.current()
    full = policy_path.read_text()

    _write(policy_path, full[: len(full) // 2])
    assert await store.poll_once() is False
    assert store.current() is old

    policy_path.unlink()
    assert await store.poll_once() is False
    assert store.current() is old
    assert _last(audit).check == "policy_rejected"
    assert "cannot read" in _last(audit).reason
    count = len(audit.records)
    assert await store.poll_once() is False  # still missing: not audited again
    assert len(audit.records) == count

    _write(policy_path, VALID_STRICT)
    assert await store.poll_once() is True
    assert store.current().policy.jev_threshold == 0.4


async def test_replace_writes_file_and_swaps(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    loaded = await store.save(VALID_STRICT)

    assert policy_path.read_bytes() == VALID_STRICT.encode("utf-8")
    assert store.current() is loaded
    assert loaded.policy.jev_threshold == 0.4
    rec = _last(audit)
    assert (rec.check, rec.policy_version) == ("policy_loaded", loaded.version)
    # The poll loop must not reload and re-audit our own write.
    count = len(audit.records)
    assert await store.poll_once() is False
    assert len(audit.records) == count
    assert list(policy_path.parent.iterdir()) == [policy_path]  # no temp file left behind


@pytest.mark.parametrize("text", [c[1] for c in INVALID_EDITS], ids=[c[0] for c in INVALID_EDITS])
async def test_replace_invalid_leaves_file_untouched(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink, text: str
) -> None:
    old = store.current()
    before = policy_path.read_bytes()

    with pytest.raises(PolicyRejectedError) as exc:
        await store.save(text)

    assert exc.value.errors
    assert policy_path.read_bytes() == before
    assert store.current() is old
    rec = _last(audit)
    assert (rec.check, rec.action, rec.policy_version) == (
        "policy_rejected",
        Action.BLOCK,
        old.version,
    )
    assert list(policy_path.parent.iterdir()) == [policy_path]


async def test_validate_has_no_side_effects(
    store: PolicyStore, policy_path: Path, audit: MemoryAuditSink
) -> None:
    old, before, count = store.current(), policy_path.read_bytes(), len(audit.records)

    assert store.validate(VALID_STRICT) == []
    assert store.validate("bogus: 1") != []

    assert store.current() is old
    assert policy_path.read_bytes() == before
    assert len(audit.records) == count


async def test_version_tracks_content(store: PolicyStore) -> None:
    initial = store.current().version
    strict = (await store.save(VALID_STRICT)).version
    back = (await store.save(_policy_yaml())).version

    assert strict != initial
    assert back == initial
    assert strict.startswith("0.2+") and initial.startswith("0.2+")


async def test_snapshot_survives_swap(store: PolicyStore) -> None:
    snapshot = store.current()
    await store.save(VALID_STRICT)

    assert snapshot.policy.jev_threshold == 0.6
    assert snapshot.version != store.current().version
    assert store.current().policy.jev_threshold == 0.4


async def test_poll_loop_applies_edit_within_two_seconds(
    store: PolicyStore, policy_path: Path
) -> None:
    store.start()  # the production interval
    try:
        _write(policy_path, VALID_STRICT)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        while store.current().policy.jev_threshold != 0.4 and loop.time() < deadline:
            await asyncio.sleep(0.05)
        assert store.current().policy.jev_threshold == 0.4
    finally:
        await store.stop()


class _FlakySink(MemoryAuditSink):
    """Fails the first write after `armed` is set, like a database that drops out once."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False

    async def write(self, record: AuditRecord) -> None:
        if self.armed:
            self.armed = False
            raise RuntimeError("audit sink down")
        await super().write(record)


async def test_poll_loop_survives_exceptions(
    policy_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sink = _FlakySink()
    store = PolicyStore(policy_path, sink)
    await store.load_initial()
    store.start(interval_s=0.02)
    try:
        sink.armed = True
        _write(policy_path, _policy_yaml(_set(["bogus"], 1)))
        while sink.armed:
            await asyncio.sleep(0.02)

        _write(policy_path, VALID_STRICT)
        for _ in range(100):
            if store.current().policy.jev_threshold == 0.4:
                break
            await asyncio.sleep(0.02)
        assert store.current().policy.jev_threshold == 0.4
    finally:
        await store.stop()
    assert any("policy poll" in r.message and r.levelno == logging.ERROR for r in caplog.records)
