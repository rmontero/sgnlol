"""Environment-only secrets and validated, reloadable routing configuration."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceRule(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    recipients: list[str] = Field(default_factory=list)
    threshold: float | None = Field(default=None, ge=0, le=1)

    enabled: bool = True
    override_recipients: bool = False
    mentions: list[str] = Field(default_factory=list, max_length=20)
    include_keywords: list[str] = Field(default_factory=list, max_length=30)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("mentions")
    @classmethod
    def valid_mentions(cls, values):
        if any(not re.fullmatch(r"(?:U|W|S)[A-Z0-9]{2,30}", v) for v in values):
            raise ValueError("Mentions must be Slack user IDs or user-group IDs")
        return list(dict.fromkeys(values))

    @field_validator("include_keywords", "exclude_keywords")
    @classmethod
    def valid_keywords(cls, values):
        if any(not v.strip() or len(v) > 100 for v in values):
            raise ValueError("Keywords must contain 1 to 100 characters")
        return list(dict.fromkeys(v.strip() for v in values))

    @field_validator("recipients")
    @classmethod
    def valid_recipients(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Recipients must be nonempty and have no surrounding whitespace")
        return list(dict.fromkeys(values))


class RepoConfig(SourceRule):
    event_types: list[Literal["pull_request", "issue_comment", "pull_request_review", "pull_request_review_comment"]] = Field(
        default_factory=lambda: ["pull_request", "issue_comment", "pull_request_review", "pull_request_review_comment"], min_length=1)


class OrgConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1)
    github_org: str = Field(min_length=1)
    slack_team_id: str = Field(min_length=1)
    type: Literal["startup", "enterprise"]
    recipient: str | None = Field(default=None, min_length=1)
    threshold: float = Field(default=0.7, ge=0, le=1)
    repos: dict[str, RepoConfig] = Field(default_factory=dict)
    slack_channels: list[str] = Field(default_factory=list)
    slack_rules: dict[str, SourceRule] = Field(default_factory=dict)

    @field_validator("id", "github_org", "slack_team_id", "recipient")
    @classmethod
    def no_blank_identifiers(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or value.strip() != value):
            raise ValueError("Identifiers must be nonempty and have no surrounding whitespace")
        return value

    @field_validator("slack_channels")
    @classmethod
    def valid_channels(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError("Channel IDs must be nonempty and have no surrounding whitespace")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_repositories(self) -> "OrgConfig":
        if not set(self.slack_rules) <= set(self.slack_channels):
            raise ValueError("Slack rules must reference configured channels")
        normalized = set()
        for repo in self.repos:
            parts = repo.split("/")
            if len(parts) != 2 or not all(parts) or repo.strip() != repo:
                raise ValueError("Repository keys must be full owner/name identifiers")
            if parts[0].casefold() != self.github_org.casefold():
                raise ValueError("Repository owner must match the configured GitHub organization")
            if repo.casefold() in normalized:
                raise ValueError("Duplicate GitHub repository identity")
            normalized.add(repo.casefold())
        if self.type == "startup" and not self.recipient:
            raise ValueError("Startup organizations require a recipient")
        return self


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orgs: list[OrgConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_identities(self) -> "AppConfig":
        for key in ("id", "github_org", "slack_team_id"):
            values = [getattr(org, key) for org in self.orgs]
            if key == "github_org":
                values = [value.casefold() for value in values]
            if len(values) != len(set(values)):
                raise ValueError(f"Duplicate organization {key}")
        return self


@dataclass
class Settings:
    database_path: str = "data/triage.sqlite3"
    config_path: str = "config/orgs.yaml"
    github_webhook_secret: str = field(default="", repr=False)
    slack_signing_secret: str = field(default="", repr=False)
    slack_bot_token: str = field(default="", repr=False)
    slack_bot_user_id: str = ""
    openai_api_key: str = field(default="", repr=False)
    openai_webhook_secret: str = field(default="", repr=False)
    dashboard_username: str = "rob"
    dashboard_password: str = field(default="", repr=False)
    redis_url: str = field(default="", repr=False)
    cache_ttl_seconds: int = 5
    model: str = "gpt-4.1-mini"
    batch_window_seconds: float = 10
    max_attempts: int = 3
    scoring_timeout_seconds: float = 20
    fail_safe: str = "forward"

    def __post_init__(self) -> None:
        if not self.database_path or not self.config_path or not self.model.strip():
            raise ValueError("Database path, config path, and model must be nonempty")
        if not 1 <= self.cache_ttl_seconds <= 60:
            raise ValueError("CACHE_TTL_SECONDS must be between 1 and 60")
        if self.fail_safe not in {"forward", "dead_letter"}:
            raise ValueError("FAIL_SAFE must be forward or dead_letter")
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise ValueError("MAX_ATTEMPTS must be a positive integer")
        if not math.isfinite(self.batch_window_seconds) or self.batch_window_seconds < 0:
            raise ValueError("BATCH_WINDOW_SECONDS must be finite and nonnegative")
        if not math.isfinite(self.scoring_timeout_seconds) or self.scoring_timeout_seconds <= 0:
            raise ValueError("SCORING_TIMEOUT_SECONDS must be finite and positive")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_path=os.getenv("DATABASE_PATH", "data/triage.sqlite3"),
            config_path=os.getenv("CONFIG_PATH", "config/orgs.yaml"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            slack_signing_secret=os.getenv("SLACK_SIGNING_SECRET", ""),
            slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
            slack_bot_user_id=os.getenv("SLACK_BOT_USER_ID", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_webhook_secret=os.getenv("OPENAI_WEBHOOK_SECRET", ""),
            dashboard_username=os.getenv("DASHBOARD_USERNAME", "rob"),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            redis_url=os.getenv("REDIS_URL", ""),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "5")),
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            batch_window_seconds=float(os.getenv("BATCH_WINDOW_SECONDS", "10")),
            max_attempts=int(os.getenv("MAX_ATTEMPTS", "3")),
            scoring_timeout_seconds=float(os.getenv("SCORING_TIMEOUT_SECONDS", "20")),
            fail_safe=os.getenv("FAIL_SAFE", "forward"),
        )


def load_config(path: str | Path) -> AppConfig:
    """Read each time so edits take effect without a restart; invalid edits fail closed."""
    path = Path(path)
    override = path.with_name(path.name + '.admin.yaml')
    if override.exists():
        path = override
    try:
        with Path(path).open(encoding="utf-8") as stream:
            document = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError("Invalid routing YAML") from exc
    if document is None:
        raise ValueError("Routing configuration must be a mapping with an orgs list")
    return AppConfig.model_validate(document)
