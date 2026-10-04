from pydantic import BaseModel


class Redaction(BaseModel):
    kind: str
    start: int
    end: int
    replacement: str
    message_index: int  # -1 for the reply
