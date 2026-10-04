"""Token authentication: who is calling, and with which roles.

The agent signs a JWT with a secret it shares with the layer (HS256). One decode call proves the
token was signed with that secret and not changed, and that `exp` is still in the future.
"""

import os

import jwt

from app.models import Identity

SECRET_ENV = "JWT_SECRET"
# The only accepted algorithm. Passing a list is what stops a token from choosing its own, such as
# "none" or an RS256 token checked against the shared secret.
ALGORITHMS = ["HS256"]
# Claims a token must carry. `exp` is required so a token cannot be valid forever.
REQUIRED_CLAIMS = ["exp", "sub"]


class InvalidTokenError(Exception):
    """The token is missing, malformed, badly signed, expired, or lacks a claim we need.

    The message names the reason but never quotes the token.
    """


def secret_from_env() -> str:
    """The shared secret. Raises when unset, so the layer never starts without one."""
    secret = os.environ.get(SECRET_ENV, "")
    if not secret:
        raise RuntimeError(f"{SECRET_ENV} is not set: the layer cannot verify tokens")
    return secret


class JwtAuthenticator:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("the JWT secret must not be empty")
        self._secret = secret

    def authenticate(self, token: str) -> Identity:
        """The identity in a valid token. Raises InvalidTokenError otherwise."""
        try:
            claims = jwt.decode(
                token,
                self._secret,
                algorithms=ALGORITHMS,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            # The type name says why (ExpiredSignatureError, InvalidSignatureError, ...).
            raise InvalidTokenError(type(exc).__name__) from exc

        user = claims["sub"]
        roles = claims.get("roles")
        if not isinstance(user, str) or not user:
            raise InvalidTokenError("the sub claim must be a non-empty string")
        if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
            raise InvalidTokenError("the roles claim must be a list of role names")
        return Identity(user=user, roles=roles)
