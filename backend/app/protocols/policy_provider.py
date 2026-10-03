from typing import Protocol

from app.models import PolicyError, PolicySnapshot


class PolicyRejectedError(Exception):
    """Raised by `save` when the submitted policy is invalid. The old policy stays active."""

    def __init__(self, errors: list[PolicyError]) -> None:
        super().__init__(f"policy rejected with {len(errors)} error(s)")
        self.errors = errors


class PolicyProvider(Protocol):
    """Source of the active policy. The file is the single source of truth."""

    def current(self) -> PolicySnapshot:
        """The active snapshot. A request calls this once and keeps the result."""
        ...

    def validate(self, yaml: str) -> list[PolicyError]:
        """Field-level errors. An empty list means the policy is valid."""
        ...

    def save(self, yaml: str) -> PolicySnapshot:
        """Validate, write the file and activate. Raises PolicyRejectedError if invalid."""
        ...
