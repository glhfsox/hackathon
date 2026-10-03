"""Data-driven cases: every `tests/cases/<check_id>.yaml` entry runs through the real pipeline.

The format is described in the team brief and docs/architecture.md §10. Jev is always mocked.
"""

from __future__ import annotations

import copy
import importlib
import importlib.util
import pkgutil
from pathlib import Path
from typing import Any, Literal

import pytest
import yaml
from pydantic import BaseModel, ConfigDict, Field

import app.checks
from app.checks.base import Check, Signature
from app.core.budget import UsageLedger
from app.core.pipeline import run_checkpoint
from app.models import Action, CanonicalRequest, Checkpoint, Message, ToolDef
from app.models.policy import Policy
from app.observability.sinks import MemoryAuditSink
from tests.conftest import FakeJudge

BACKEND_DIR = Path(__file__).resolve().parents[1]
POLICY_PATH = BACKEND_DIR / "policy.yaml"
CASES_DIR = Path(__file__).resolve().parent / "cases"

if not POLICY_PATH.exists():
    pytest.skip(
        f"{POLICY_PATH} does not exist yet (written by the policy store); case tests skipped",
        allow_module_level=True,
    )

BASE_POLICY: dict[str, Any] = yaml.safe_load(POLICY_PATH.read_text())


class _Strict(BaseModel):
    # A typo in a case file must fail the case, not silently drop an assertion.
    model_config = ConfigDict(extra="forbid")


class Usage(_Strict):
    requests_last_minute: int = 0
    tokens_today: int = 0
    cost_today: float = 0.0


class Expect(_Strict):
    action: Action
    check: str | None = None
    reason_contains: str | None = None
    content_contains: str | None = None
    content_not_contains: str | None = None


class JudgeSpec(_Strict):
    score: float = 0.0
    reason: str = "mocked judge"
    categories: list[str] = Field(default_factory=list)
    decided_by: Literal["jev", "fallback"] = "jev"


class Case(_Strict):
    name: str
    checkpoint: Checkpoint
    caller: str = "demo"
    model: str = "gemma4"
    messages: list[Message]
    tools: list[ToolDef] = Field(default_factory=list)
    reply: Message | None = None
    policy_patch: dict[str, Any] = Field(default_factory=dict)
    judge: JudgeSpec | Literal["unavailable"] = Field(default_factory=JudgeSpec)
    usage: Usage = Field(default_factory=Usage)
    expect: Expect


def _load_checks() -> list[Check]:
    """Every app.checks.<id> module that exists today and exposes CHECK."""
    found = []
    for info in pkgutil.iter_modules(app.checks.__path__):
        if info.name == "base" or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"app.checks.{info.name}")
        check = getattr(module, "CHECK", None)
        if check is not None:
            found.append(check)
    return found


def _load_case_files() -> dict[Path, list[dict[str, Any]]]:
    files = {}
    for path in sorted(CASES_DIR.glob("*.yaml")):
        cases = yaml.safe_load(path.read_text()) or []
        if not isinstance(cases, list):
            raise ValueError(f"{path} must hold a YAML list of cases")
        files[path] = cases
    return files


CHECKS = _load_checks()
CASE_FILES = _load_case_files()
PARAMS = [
    pytest.param(raw, id=f"{path.name}::{raw.get('name', f'#{i}')}")
    for path, cases in CASE_FILES.items()
    for i, raw in enumerate(cases)
]


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _load_signatures(policy: Policy) -> list[Signature] | None:
    if policy.signatures is None or importlib.util.find_spec("app.core.signatures") is None:
        return None
    from app.core.signatures import load_signatures

    signatures, _version = load_signatures(policy.signatures.source, base_dir=BACKEND_DIR)
    return signatures


def _ledger(caller_id: str, usage: Usage) -> UsageLedger:
    # Preload through the public API, so the check reads exactly what production would.
    ledger = UsageLedger()
    for _ in range(usage.requests_last_minute):
        ledger.record_request(caller_id)
    if usage.tokens_today or usage.cost_today:
        ledger.record_usage(caller_id, usage.tokens_today, usage.cost_today)
    return ledger


def _all_text(request: CanonicalRequest) -> str:
    texts = [m.content for m in request.messages if m.content]
    if request.reply and request.reply.content:
        texts.append(request.reply.content)
    return "\n".join(texts)


@pytest.mark.parametrize("raw", PARAMS)
async def test_case(raw: dict[str, Any]) -> None:
    case = Case.model_validate(raw)
    policy = Policy.model_validate(_deep_merge(BASE_POLICY, case.policy_patch))
    request = CanonicalRequest(
        request_id=f"case-{case.name}",
        caller_id=case.caller,
        model=case.model,
        checkpoint=case.checkpoint,
        messages=case.messages,
        tools=case.tools,
        reply=case.reply,
    )
    judge = (
        FakeJudge(unavailable=True)
        if case.judge == "unavailable"
        else FakeJudge(**case.judge.model_dump())
    )

    decision, forwarded = await run_checkpoint(
        request,
        policy,
        policy.version,
        policy.callers[case.caller],
        ledger=_ledger(case.caller, case.usage),
        signatures=_load_signatures(policy),
        judge=judge,
        audit=MemoryAuditSink(),
        checks=CHECKS,
    )

    trace = [(r.check, r.verdict, r.action.value, r.reason) for r in decision.results]
    exp = case.expect
    assert decision.action == exp.action, trace
    if exp.check is not None:
        if exp.action == Action.BLOCK:
            assert decision.blocked_by == exp.check, trace
        assert any(r.check == exp.check and r.action == exp.action for r in decision.results), trace
    if exp.reason_contains is not None:
        reasons = [r.reason for r in decision.results if exp.check is None or r.check == exp.check]
        assert any(exp.reason_contains in reason for reason in reasons), trace
    text = _all_text(forwarded)
    if exp.content_contains is not None:
        assert exp.content_contains in text
    if exp.content_not_contains is not None:
        assert exp.content_not_contains not in text


def test_every_check_has_allow_and_block_cases() -> None:
    by_file = {path.stem: cases for path, cases in CASE_FILES.items()}
    missing = []
    for check in CHECKS:
        actions = {raw.get("expect", {}).get("action") for raw in by_file.get(check.id, [])}
        if "allow" not in actions or not actions & {"block", "redact"}:
            missing.append(check.id)
    assert not missing, f"checks without both an allow and a block/redact case: {missing}"
