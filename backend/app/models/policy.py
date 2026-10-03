"""Policy schema (provisional) and the store that keeps the active policy.

The official schema is OPEN in contracts/policy.example.yaml. These models cover only what the
proxy, the permissions check and the Jev client need today, and reject unknown keys so a typo in
the file is an error, not a silent no-op. That includes unknown check ids, a mode for a checkpoint
a check never runs at, and a check parameter the check does not take (CHECK_SPECS). The schema
owner extends them; the store does not change.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from app.models import Checkpoint, Mode

_CHECKPOINT_KEYS = {c.value for c in Checkpoint}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- what each check accepts in its policy section ------------------------------------------------

_Seconds = Annotated[float, Field(gt=0, allow_inf_nan=False)]
_Limit = Annotated[int, Field(ge=0)]
_Size = Annotated[int, Field(gt=0)]
_Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_Roles = list[Literal["system", "user", "assistant", "tool"]]
_Cost = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class _ExtraPattern(_Strict):
    kind: _Text
    pattern: str


@dataclass(frozen=True)
class CheckSpec:
    """The checkpoints a check runs at and the parameters it takes, each with its type."""

    checkpoints: frozenset[Checkpoint]
    params: dict[str, TypeAdapter[Any]]


def _spec(checkpoints: set[Checkpoint], **params: Any) -> CheckSpec:
    return CheckSpec(frozenset(checkpoints), {k: TypeAdapter(t) for k, t in params.items()})


_IN, _CALL, _RESULT, _OUT = (
    Checkpoint.INPUT,
    Checkpoint.TOOL_CALL,
    Checkpoint.TOOL_RESULT,
    Checkpoint.OUTPUT,
)
# Static on purpose: importing app.checks here would be an import cycle (app.checks.signatures ->
# app.core.signatures -> app.models.policy). tests/test_integration.py asserts it matches the
# check registry.
# Only types are checked here; value checks a check owns (known names, regex syntax) stay in it.
# Only jev takes timeout_s (the pipeline applies it): the rule checks are synchronous CPU work that
# a timeout could not interrupt, so a timeout_s there would only pretend to bound them.
CHECK_SPECS: dict[str, CheckSpec] = {
    "permissions": _spec({_IN, _CALL, _RESULT}, allowed_models=list[str], allowed_tools=list[str]),
    # A limit left out is unlimited.
    "budget": _spec(
        {_IN, _RESULT}, requests_per_minute=_Limit, tokens_per_day=_Limit, cost_per_day=_Cost
    ),
    "loop_detection": _spec({_IN, _RESULT}, max_tool_calls=_Limit, max_repeats=_Limit),
    "signatures": _spec({_IN, _CALL, _RESULT}, categories=list[str], skip_roles=_Roles),
    "tool_args": _spec({_CALL}, allowed_root=_Text, categories=list[str], max_command_chars=_Size),
    "pii_secrets": _spec(
        {_IN, _RESULT, _OUT},
        types=list[str],
        extra_patterns=list[_ExtraPattern],
        skip_roles=_Roles,
    ),
    "jev": _spec(
        {_IN, _RESULT, _OUT},
        timeout_s=_Seconds,
        max_chars=_Size,
        max_judge_calls=Annotated[int, Field(ge=1, le=1024)],
        max_concurrency=Annotated[int, Field(ge=1, le=64)],
        skip_roles=_Roles,
    ),
}
# Jev must only ever see text pii_secrets has already redacted (constitution V).
_JEV, _PII = "jev", "pii_secrets"


_Add = Callable[[tuple[str | int, ...], str, Any], None]


def _names(values: Any) -> str:
    return ", ".join(sorted(str(v.value if isinstance(v, Checkpoint) else v) for v in values))


def _check_section(
    loc: tuple[str | int, ...],
    check_id: str,
    modes: dict[Checkpoint, Mode],
    params: dict[str, Any],
    add: _Add,
) -> None:
    """Report an unknown check id, a mode for a checkpoint the check never runs at, and a
    parameter the check does not take or of the wrong type. Each would otherwise be ignored."""
    spec = CHECK_SPECS.get(check_id)
    if spec is None:
        add(loc, f"unknown check; the checks are: {_names(CHECK_SPECS)}", check_id)
        return
    for cp, mode in modes.items():
        if cp not in spec.checkpoints:
            msg = f"{check_id} never runs at {cp.value}; it runs at: {_names(spec.checkpoints)}"
            add((*loc, cp.value), msg, mode.value)
    for key, value in params.items():
        adapter = spec.params.get(key)
        if adapter is None:
            add(
                (*loc, key),
                f"unknown key for {check_id}: its checkpoints are {_names(spec.checkpoints)} "
                f"and its parameters are {_names(spec.params)}",
                value,
            )
            continue
        try:
            adapter.validate_python(value, strict=True)
        except ValidationError as exc:
            for err in exc.errors():
                add((*loc, key, *err["loc"]), err["msg"], err["input"])


class Profile(_Strict):
    """A strictness level: the Jev threshold plus per-check mode overrides on top of `checks`."""

    jev_threshold: float = Field(ge=0.0, le=1.0)
    checks: dict[str, dict[Checkpoint, Mode]] = Field(default_factory=dict)


class SignatureFeedConfig(_Strict):
    """Where the externally managed attack-signature feed lives: a file path or an http(s) URL."""

    source: str
    refresh_s: float = Field(default=10.0, gt=0)
    timeout_s: float = Field(default=5.0, gt=0)


class ModelConfig(_Strict):
    upstream_base_url: str
    price_per_1k_tokens: float = 0.0
    # Seconds to wait for the upstream reply; a local model can take tens of seconds.
    timeout_s: float = Field(default=120.0, gt=0)


class CheckSection(BaseModel):
    """One check's section: checkpoint keys are modes, every other key is a check parameter."""

    modes: dict[Checkpoint, Mode] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _split(cls, raw: Any) -> Any:
        if not isinstance(raw, dict) or "modes" in raw or "params" in raw:
            return raw
        modes = {k: v for k, v in raw.items() if k in _CHECKPOINT_KEYS}
        params = {k: v for k, v in raw.items() if k not in _CHECKPOINT_KEYS}
        return {"modes": modes, "params": params}

    def mode(self, checkpoint: Checkpoint) -> Mode:
        return self.modes.get(checkpoint, Mode.OFF)


class JevFallback(_Strict):
    model: str
    base_url: str
    timeout_s: float = 30.0


class JevConfig(_Strict):
    model: str = "jev-1.13.0"
    api_key_env: str = "TYPESAFE_API_KEY"
    base_url: str = "https://api.typesafe.ai"
    timeout_s: float = 3.0
    fallback: JevFallback

    @field_validator("model")
    @classmethod
    def _pinned(cls, v: str) -> str:
        if v == "jev-latest":
            raise ValueError("jev.model must be a pinned version, not jev-latest")
        return v


class Policy(_Strict):
    version: str
    active_profile: str
    profiles: dict[str, Profile]
    models: dict[str, ModelConfig]
    checks: dict[str, CheckSection] = Field(default_factory=dict)
    signatures: SignatureFeedConfig | None = None
    jev: JevConfig

    @model_validator(mode="after")
    def _cross_field(self) -> Policy:
        # Raised with real paths so the policy editor can point at the offending field.
        errors: list[InitErrorDetails] = []

        def add(loc: tuple[str | int, ...], msg: str, value: Any) -> None:
            err = PydanticCustomError("policy_reference", msg)
            errors.append(InitErrorDetails(type=err, loc=loc, input=value))

        if self.active_profile not in self.profiles:
            add(("active_profile",), "profile is not defined in profiles", self.active_profile)
        allowed_models = self.check_config("permissions").params.get("allowed_models", [])
        if isinstance(allowed_models, list):  # a wrong type is reported by _check_section
            for i, m in enumerate(allowed_models):
                if m not in self.models:
                    loc = ("checks", "permissions", "allowed_models", i)
                    add(loc, "model is not defined in models", m)
        for cid, section in self.checks.items():
            _check_section(("checks", cid), cid, section.modes, section.params, add)
        for name, profile in self.profiles.items():
            for cid, modes in profile.checks.items():
                _check_section(("profiles", name, "checks", cid), cid, modes, {}, add)
        self._check_pii_before_jev(add)
        if errors:
            raise ValidationError.from_exception_data("Policy", errors)
        return self

    def _check_pii_before_jev(self, add: _Add) -> None:
        """Wherever jev is on, under any profile, pii_secrets must be on too (any mode redacts
        the copy the later checks see). Otherwise raw PII would be sent to Jev."""
        offending: dict[tuple[str | int, ...], list[str]] = {}
        for name, profile in self.profiles.items():
            for cp in Checkpoint:
                if cp not in CHECK_SPECS[_JEV].checkpoints:
                    continue
                if self._mode_in(profile, _JEV, cp) == Mode.OFF:
                    continue
                if self._mode_in(profile, _PII, cp) != Mode.OFF:
                    continue
                # Point at the key that turned pii_secrets off for this profile.
                if cp in profile.checks.get(_PII, {}):
                    loc: tuple[str | int, ...] = ("profiles", name, "checks", _PII, cp.value)
                else:
                    loc = ("checks", _PII, cp.value)
                offending.setdefault(loc, []).append(name)
        for loc, names in offending.items():
            at = loc[-1]
            add(
                loc,
                f"pii_secrets is off at {at} while jev is on there (profile {', '.join(names)}): "
                f"nothing would redact PII before it is sent to Jev. Turn pii_secrets on at {at} "
                "(monitor is enough) or turn jev off there",
                Mode.OFF.value,
            )
        # A role pii_secrets skips is never redacted, so Jev must not be sent that role either.
        pii_skips = self.check_config(_PII).params.get("skip_roles", [])
        jev_skips = self.check_config(_JEV).params.get("skip_roles", [])
        if isinstance(pii_skips, list) and isinstance(jev_skips, list):
            missing = sorted(set(map(str, pii_skips)) - set(map(str, jev_skips)))
            jev_on = any(
                self._mode_in(profile, _JEV, cp) != Mode.OFF
                for profile in self.profiles.values()
                for cp in CHECK_SPECS[_JEV].checkpoints
            )
            if missing and jev_on:
                add(
                    ("checks", _JEV, "skip_roles"),
                    f"pii_secrets skips {', '.join(missing)}, so that text is never redacted; "
                    "jev must skip the same roles or unredacted text would be sent to Jev",
                    jev_skips,
                )

    @property
    def profile(self) -> Profile:
        return self.profiles[self.active_profile]

    def check_config(self, check_id: str) -> CheckSection:
        return self.checks.get(check_id, CheckSection())

    def mode(self, check_id: str, checkpoint: Checkpoint) -> Mode:
        """Effective mode: the active profile's override wins over the check's base mode."""
        return self._mode_in(self.profile, check_id, checkpoint)

    def _mode_in(self, profile: Profile, check_id: str, checkpoint: Checkpoint) -> Mode:
        override = profile.checks.get(check_id, {}).get(checkpoint)
        return override if override is not None else self.check_config(check_id).mode(checkpoint)
