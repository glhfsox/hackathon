from pydantic import BaseModel


class Identity(BaseModel):
    """Who is calling, as proven by a verified token. The policy maps roles to tools."""

    user: str  # the token's `sub`
    roles: list[str]
