"""Verify OpenAI deliveries locally and validate their event envelope."""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field
from standardwebhooks import Webhook


class OpenAIEvent(BaseModel):
    # Preserve new provider fields in the durable receipt without rejecting new types.
    model_config = ConfigDict(extra="allow", strict=True)
    id: str = Field(min_length=1, max_length=256)
    type: str = Field(min_length=1, max_length=256)
    created_at: int = Field(ge=0)
    data: dict


def verify_openai(body: bytes, headers: Mapping[str, str], secret: str) -> None:
    # Verification is entirely local. No API credential or outbound API call is needed.
    Webhook(secret).verify(body, dict(headers), json_parse=False)
