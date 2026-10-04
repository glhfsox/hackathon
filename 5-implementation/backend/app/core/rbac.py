"""Role-based tool access: which tools the model is shown, and which calls it may make.

Both functions are pure and vendor neutral: they work on canonical models. The caller passes the
tools the user may use (Policy.allowed_tools) and logs the names that come back.
"""

from collections.abc import Set

from app.checks.base import safe_label
from app.models import CanonicalRequest, Message


def filter_tools(
    request: CanonicalRequest, allowed: Set[str]
) -> tuple[CanonicalRequest, list[str]]:
    """The request without the tools the user may not use, so the model never sees them.

    Returns the request and the names removed, in the order the agent offered them.
    """
    removed = [tool.name for tool in request.tools if tool.name not in allowed]
    if not removed:
        return request, []
    kept = [tool for tool in request.tools if tool.name in allowed]
    return request.model_copy(update={"tools": kept}), removed


def deny_tool_calls(reply: Message, allowed: Set[str]) -> tuple[Message, list[str]]:
    """The reply with every call to a tool the user may not use replaced by a text notice.

    Calls to allowed tools stay. Returns the reply and the names denied, in call order. A model
    can ask for a tool it was never shown, so this runs on every reply whatever filter_tools did.
    """
    denied = [call.name for call in reply.tool_calls if call.name not in allowed]
    if not denied:
        return reply, []
    kept = [call for call in reply.tool_calls if call.name in allowed]
    # The model chooses tool names: safe_label keeps anything but a plain name out of the text.
    names = ", ".join(repr(safe_label(name)) for name in dict.fromkeys(denied))
    notice = f"Tool call denied by policy: {names}."
    content = f"{reply.content}\n{notice}" if reply.content else notice
    return reply.model_copy(update={"tool_calls": kept, "content": content}), denied
