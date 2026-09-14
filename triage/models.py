"""Validated values shared by the ingestion and delivery pipeline."""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1)
    org_id: str = Field(min_length=1)
    source: Literal["github", "slack"]
    kind: str = Field(min_length=1)
    subject_key: str = Field(min_length=1)
    repo: str | None = None
    actor: str = ""
    text: str = ""
    url: str = ""
    received_at: float = Field(default_factory=time.time, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    score: float = Field(ge=0, le=1)
    rationale: str
    summary: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    fallback: bool = False


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    recipient: str = Field(min_length=1)
    threshold: float = Field(ge=0, le=1)
