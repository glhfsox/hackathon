from pydantic import BaseModel


class PolicyError(BaseModel):
    """One field-level problem found while validating a policy."""

    loc: str  # path to the offending field, e.g. "callers.support.budget"
    msg: str
