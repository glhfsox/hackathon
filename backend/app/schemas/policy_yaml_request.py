from pydantic import BaseModel


class PolicyYamlRequest(BaseModel):
    """Body of POST /api/policy/validate and PUT /api/policy."""

    yaml: str
