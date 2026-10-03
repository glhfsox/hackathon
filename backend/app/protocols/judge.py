from typing import Protocol

from app.models import JudgeInput, JudgeVerdict


class Judge(Protocol):
    """AI decision maker. Jev and the local fallback model both implement it.

    Raises on timeout, transport error or unparseable output, so the caller can try the
    fallback and fail closed when none answers.
    """

    async def judge(self, payload: JudgeInput) -> JudgeVerdict: ...
