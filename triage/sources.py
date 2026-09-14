"""Versioned, atomic admin routing edits on the existing persistent volume."""
import hashlib
import os
import re
import tempfile
from pathlib import Path
from threading import Lock

import yaml
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from .config import AppConfig, RepoConfig, SourceRule, load_config


class SourceInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(min_length=64, max_length=64)
    org_id: str = Field(min_length=1, max_length=200)
    source: Literal['slack', 'github']
    identity: str = Field(min_length=1, max_length=250)
    rule: RepoConfig


class SourceConflict(ValueError):
    pass


class Sources:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = Lock()

    @staticmethod
    def snapshot(config):
        return {'orgs': config.model_dump()['orgs'],
                'revision': hashlib.sha256(config.model_dump_json().encode()).hexdigest()}

    def read(self):
        return self.snapshot(load_config(self.path))

    def save(self, value):
        with self.lock:
            config = load_config(self.path)
            if value.revision != self.snapshot(config)['revision']:
                raise SourceConflict('Sources changed. Reload before saving.')
            org = next((o for o in config.orgs if o.id == value.org_id), None)
            if org is None:
                raise ValueError('Unknown organization')
            if any(not re.fullmatch(r'[UWCGD][A-Z0-9]{2,30}', r) for r in value.rule.recipients):
                raise ValueError('Alert destinations must be Slack user or channel IDs. Put groups in Mentions.')
            value.rule.override_recipients = bool(value.rule.recipients)
            if value.source == 'slack':
                if not re.fullmatch(r'[CG][A-Z0-9]{2,30}', value.identity):
                    raise ValueError('Enter a Slack channel ID beginning with C or G')
                if value.identity not in org.slack_channels:
                    org.slack_channels.append(value.identity)
                org.slack_rules[value.identity] = SourceRule.model_validate(value.rule.model_dump(exclude={'event_types'}))
            else:
                if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', value.identity):
                    raise ValueError('Enter a GitHub repository as owner/name')
                identity = next((name for name in org.repos if name.casefold() == value.identity.casefold()), value.identity)
                org.repos[identity] = value.rule
            config = AppConfig.model_validate(config.model_dump())
            raw = yaml.safe_dump(config.model_dump(), sort_keys=False)
            if len(raw.encode()) > 65536:
                raise ValueError('Routing configuration is too large')
            target = self.path.with_name(self.path.name + '.admin.yaml')
            if target.is_symlink():
                raise ValueError('Routing override must be a regular file')
            fd, temporary = tempfile.mkstemp(prefix='.routing-', dir=target.parent)
            try:
                with os.fdopen(fd, 'w') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                directory = os.open(target.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return self.snapshot(config)
