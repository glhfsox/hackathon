from pydantic import BaseModel, Field


class Usage(BaseModel):
    """Upstream usage of one model call, handed to the pipeline at the reply checkpoints."""

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    upstream_latency_ms: float | None = Field(default=None, ge=0.0)
