"""The dev policy file and the Policy schema helpers the pipeline relies on (spec US2)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.core.policy_store import parse_policy
from app.models.policy import CHECK_SPECS, Policy
from app.protocols.policy_provider import PolicyRejectedError

POLICY_FILE = Path(__file__).parent.parent / "policy.yaml"


@pytest.fixture
def policy() -> Policy:
    return parse_policy(POLICY_FILE.read_text())


def _raw() -> dict[str, Any]:
    return yaml.safe_load(POLICY_FILE.read_text())


def _errors(mutate: Callable[[dict[str, Any]], None]) -> list[dict[str, str]]:
    """Field-level errors of the dev policy after `mutate`; fails if the edit is accepted."""
    raw = _raw()
    mutate(raw)
    with pytest.raises(PolicyRejectedError) as exc:
        parse_policy(yaml.safe_dump(raw))
    return exc.value.errors


def _accepts(mutate: Callable[[dict[str, Any]], None]) -> Policy:
    raw = _raw()
    mutate(raw)
    return parse_policy(yaml.safe_dump(raw))


def _rename(section: dict[str, Any], old: str, new: str) -> None:
    section[new] = section.pop(old)


def test_dev_policy_validates(policy: Policy) -> None:
    assert policy.jev_threshold == 0.6
    assert set(policy.checks) == {
        "permissions",
        "budget",
        "loop_detection",
        "signatures",
        "tool_args",
        "pii_secrets",
        "jev",
    }
    assert all(policy.enabled(cid) for cid in CHECK_SPECS)
    assert policy.jev.model != "jev-latest"


def test_check_sections_hold_params_only(policy: Policy) -> None:
    assert policy.check_config("loop_detection").params == {"max_tool_calls": 10, "max_repeats": 3}
    tool_args = policy.check_config("tool_args")
    assert set(tool_args.params) == {"allowed_root", "categories", "max_command_chars"}
    permissions = policy.check_config("permissions")
    assert set(permissions.params) == {"allowed_models", "allowed_tools"}
    budget = policy.check_config("budget")
    assert budget.params == {
        "requests_per_minute": 60,
        "tokens_per_day": 200000,
        "cost_per_day": 1.0,
    }
    assert not policy.enabled("no_such_check")
    assert policy.check_config("no_such_check").params == {}


def test_a_bare_section_turns_the_check_on_with_defaults() -> None:
    text = POLICY_FILE.read_text()
    start = text.index("  loop_detection:\n")
    end = text.index("\n\n", start)
    policy = parse_policy(text[:start] + "  loop_detection:" + text[end:])
    assert policy.enabled("loop_detection")
    assert policy.check_config("loop_detection").params == {}


def test_dev_policy_usability_params(policy: Policy) -> None:
    params = {cid: policy.check_config(cid).params for cid in CHECK_SPECS}
    assert params["signatures"]["skip_roles"] == ["system"]
    assert params["pii_secrets"]["skip_roles"] == ["system"]
    assert params["tool_args"]["max_command_chars"] == 2000
    assert params["jev"]["max_judge_calls"] == 128
    assert params["jev"]["max_concurrency"] == 8
    # Rule checks are synchronous CPU work: a timeout cannot interrupt them.
    assert [cid for cid, p in params.items() if "timeout_s" in p] == ["jev"]


# --- a typo is an error, never a silently disabled check (judges edit the file live) ------------


@pytest.mark.parametrize(
    ("mutate", "loc"),
    [
        # The check would silently be off everywhere.
        (lambda r: _rename(r["checks"], "pii_secrets", "pii_secret"), "checks.pii_secret"),
        # The limit would silently disappear.
        (
            lambda r: _rename(r["checks"]["loop_detection"], "max_tool_calls", "max_tool_cals"),
            "checks.loop_detection.max_tool_cals",
        ),
        # A parameter that belongs to another check.
        (lambda r: r["checks"]["jev"].update(types=["email"]), "checks.jev.types"),
    ],
    ids=["check_id", "param", "foreign_param"],
)
def test_unknown_check_ids_and_params_are_rejected(mutate, loc: str) -> None:
    errors = _errors(mutate)
    assert loc in [e.loc for e in errors], errors


# --- modes and profiles were removed: an old policy is rejected with a reason -----------------


@pytest.mark.parametrize("checkpoint", ["input", "tool_call", "tool_result", "output"])
def test_an_old_mode_key_is_rejected_with_a_reason(checkpoint: str) -> None:
    errors = _errors(lambda r: r["checks"]["budget"].update({checkpoint: "block"}))
    [err] = [e for e in errors if e.loc == f"checks.budget.{checkpoint}"]
    assert "modes were removed" in err.msg


@pytest.mark.parametrize(
    ("key", "value"),
    [("active_profile", "strict"), ("profiles", {"strict": {"jev_threshold": 0.4}})],
)
def test_old_profile_keys_are_rejected_with_a_reason(key: str, value: Any) -> None:
    errors = _errors(lambda r: r.update({key: value}))
    [err] = [e for e in errors if e.loc == key]
    assert "profiles were removed" in err.msg


@pytest.mark.parametrize("value", [-0.1, 1.1, "high"])
def test_bad_jev_threshold_is_rejected(value: Any) -> None:
    errors = _errors(lambda r: r.update(jev_threshold=value))
    assert "jev_threshold" in [e.loc for e in errors]


@pytest.mark.parametrize(
    ("check", "param", "value", "loc_suffix"),
    [
        ("loop_detection", "max_tool_calls", "ten", ""),
        ("loop_detection", "max_tool_calls", None, ""),
        ("loop_detection", "max_repeats", -1, ""),
        ("loop_detection", "max_repeats", True, ""),
        ("loop_detection", "max_repeats", 2.5, ""),
        ("jev", "max_chars", 0, ""),
        ("jev", "max_judge_calls", -3, ""),
        ("jev", "max_judge_calls", 0, ""),
        ("jev", "max_judge_calls", 1025, ""),
        ("jev", "max_concurrency", 0, ""),
        ("jev", "max_concurrency", 65, ""),
        ("jev", "max_concurrency", "8", ""),
        ("jev", "timeout_s", 0, ""),
        ("jev", "timeout_s", "fast", ""),
        ("jev", "timeout_s", float("inf"), ""),
        ("tool_args", "max_command_chars", 0, ""),
        ("tool_args", "max_command_chars", True, ""),
        ("signatures", "skip_roles", "system", ""),
        ("signatures", "skip_roles", ["admin"], ".0"),
        ("pii_secrets", "skip_roles", ["system", "System"], ".1"),
        ("pii_secrets", "types", "email", ""),
        ("pii_secrets", "types", ["email", 1], ".1"),
        ("pii_secrets", "extra_patterns", "EMP-\\d+", ""),
        ("pii_secrets", "extra_patterns", [{"kind": "employee_id"}], ".0.pattern"),
        ("pii_secrets", "extra_patterns", [{"kind": " ", "pattern": "x"}], ".0.kind"),
        ("tool_args", "allowed_root", "", ""),
        ("tool_args", "allowed_root", 5, ""),
        ("tool_args", "categories", "shell", ""),
        ("signatures", "categories", [1], ".0"),
    ],
)
def test_bad_param_values_are_rejected_at_load(
    check: str, param: str, value: Any, loc_suffix: str
) -> None:
    errors = _errors(lambda r: r["checks"][check].update({param: value}))
    assert f"checks.{check}.{param}{loc_suffix}" in [e.loc for e in errors], errors


@pytest.mark.parametrize(
    ("check", "param", "value"),
    [
        # 0 is a real limit for the loop check: no tool call at all.
        ("loop_detection", "max_tool_calls", 0),
        ("jev", "timeout_s", 2.5),
        ("jev", "max_judge_calls", 1024),
        ("jev", "max_concurrency", 1),
        ("jev", "max_concurrency", 64),
        ("tool_args", "max_command_chars", 1),
        ("signatures", "skip_roles", []),
        ("signatures", "skip_roles", ["system", "user", "assistant", "tool"]),
        ("pii_secrets", "skip_roles", ["system"]),
        ("pii_secrets", "extra_patterns", [{"kind": "employee_id", "pattern": "EMP-\\d{6}"}]),
        # Value checks the check owns (known category names, regex syntax) stay at runtime.
        ("signatures", "categories", ["code_execution"]),
        ("pii_secrets", "extra_patterns", [{"kind": "broken", "pattern": "("}]),
    ],
)
def test_good_param_values_are_accepted(check: str, param: str, value: Any) -> None:
    policy = _accepts(lambda r: r["checks"][check].update({param: value}))
    assert policy.check_config(check).params[param] == value


@pytest.mark.parametrize("check", sorted(set(CHECK_SPECS) - {"jev"}))
def test_timeout_s_is_only_a_jev_param(check: str) -> None:
    # Rule checks are synchronous CPU work, so a timeout could never interrupt them: a timeout_s
    # there would only pretend to bound them.
    errors = _errors(lambda r: r["checks"][check].update(timeout_s=5))
    [err] = [e for e in errors if e.loc == f"checks.{check}.timeout_s"]
    assert "unknown key" in err.msg


# --- YAML that would silently change what the policy says is an error --------------------------


def _text_errors(text: str) -> list[dict[str, str]]:
    with pytest.raises(PolicyRejectedError) as exc:
        parse_policy(text)
    return exc.value.errors


@pytest.mark.parametrize(
    ("text", "loc", "line"),
    [
        # A second section for one check: YAML keeps only the last, so allowed_root would vanish.
        (
            "checks:\n  tool_args:\n    tool_call: block\n    allowed_root: /workspace\n"
            "  tool_args: {tool_call: block}\n",
            "checks.tool_args",
            5,
        ),
        # A parameter written twice in one section: the later one would win.
        (
            "checks:\n  signatures: {skip_roles: [system], skip_roles: []}\n",
            "checks.signatures.skip_roles",
            2,
        ),
        # A second jev_threshold would silently change it.
        ("jev_threshold: 0.4\nversion: '1'\njev_threshold: 0.9\n", "jev_threshold", 3),
        (
            "checks:\n  pii_secrets:\n    extra_patterns:\n"
            "      - {kind: a, pattern: x, kind: b}\n",
            "checks.pii_secrets.extra_patterns.0.kind",
            4,
        ),
    ],
    ids=["section", "param_in_section", "top_level", "inside_list"],
)
def test_duplicate_keys_are_rejected(text: str, loc: str, line: int) -> None:
    [err] = _text_errors(text)
    assert err.loc == loc
    key = loc.rsplit(".", 1)[-1]
    assert f"'{key}'" in err.msg and f"line {line}" in err.msg, err


def test_duplicate_section_in_dev_policy_is_rejected() -> None:
    text = POLICY_FILE.read_text().replace("\nchecks:\n", "\nchecks:\n  tool_args: {}\n")
    errors = _text_errors(text)
    assert [e.loc for e in errors] == ["checks.tool_args"]


@pytest.mark.parametrize("word", ["on", "off", "yes", "no", "On", "NO"])
def test_yaml_11_bool_words_stay_strings(word: str) -> None:
    text = POLICY_FILE.read_text().replace('version: "0.2"', f"version: {word}")
    assert parse_policy(text).version == word


def test_true_and_false_stay_booleans() -> None:
    errors = _text_errors(POLICY_FILE.read_text().replace('version: "0.2"', "version: true"))
    assert [e.loc for e in errors] == ["version"]


# --- Jev never judges text nothing redacted (constitution V) ------------------------------------


def test_jev_on_while_pii_is_off_is_rejected() -> None:
    errors = _errors(lambda r: r["checks"].pop("pii_secrets"))
    [err] = [e for e in errors if e.loc == "checks.pii_secrets"]
    assert "jev" in err.msg


def test_jev_and_pii_both_off_is_allowed() -> None:
    def mutate(r: dict[str, Any]) -> None:
        del r["checks"]["pii_secrets"]
        del r["checks"]["jev"]

    policy = _accepts(mutate)
    assert not policy.enabled("jev") and not policy.enabled("pii_secrets")


def test_jev_must_skip_every_role_pii_skips() -> None:
    errors = _errors(lambda r: r["checks"]["jev"].update(skip_roles=[]))
    assert "checks.jev.skip_roles" in [e.loc for e in errors]


def test_a_params_key_in_a_section_is_an_unknown_param() -> None:
    # It used to be taken as the internal wrapper, which silently dropped its siblings.
    errors = _errors(lambda r: r["checks"]["pii_secrets"].update(params={}))
    assert "checks.pii_secrets.params" in [e.loc for e in errors], errors
