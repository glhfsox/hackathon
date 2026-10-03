"""Check registry. Order is cost_rank ascending (docs/architecture.md §4).

Adding a check: write app/checks/<id>.py with a module-level CHECK and add its name below.
"""

from __future__ import annotations

from importlib import import_module

from app.checks.base import Check

_MODULES = (
    "permissions",
    "budget",
    "loop_detection",
    "signatures",
    "tool_args",
    "pii_secrets",
    "jev",
)

_REGISTRY: dict[str, Check] | None = None


def _load() -> dict[str, Check]:
    # Imported lazily: check modules import app.checks.base, which imports this package.
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = {}
        for name in _MODULES:
            check: Check = import_module(f"app.checks.{name}").CHECK
            if check.id in _REGISTRY:
                raise ValueError(f"check {check.id!r} registered twice")
            _REGISTRY[check.id] = check
    return _REGISTRY


def ordered_checks() -> list[Check]:
    return sorted(_load().values(), key=lambda c: c.cost_rank)


def get_check(check_id: str) -> Check:
    return _load()[check_id]
