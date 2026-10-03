from pydantic import BaseModel

from app.models.enums import Checkpoint


class JudgeInput(BaseModel):
    checkpoint: Checkpoint
    text: str  # already redacted
    context: str = ""  # short summary: caller role, tool names
