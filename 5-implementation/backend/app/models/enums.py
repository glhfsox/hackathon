from enum import StrEnum


class Checkpoint(StrEnum):
    INPUT = "input"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OUTPUT = "output"


class Action(StrEnum):
    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"
    FLAG = "flag"


class Mode(StrEnum):
    """Policy mode of a check at one checkpoint."""

    OFF = "off"
    MONITOR = "monitor"
    REDACT = "redact"
    BLOCK = "block"


class DecidedBy(StrEnum):
    RULES = "rules"
    JEV = "jev"
    FALLBACK = "fallback"
