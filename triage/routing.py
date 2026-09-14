"""Resolve only destinations explicitly authorized by current configuration."""

from triage.config import AppConfig, SourceRule
from triage.models import Event, Route


def source_rule(event, config):
    org = next((org for org in config.orgs if org.id == event.org_id), None)
    if org is None:
        return None
    if event.source == "github":
        return next((rule for name, rule in org.repos.items() if name.casefold() == (event.repo or "").casefold()), None)
    channel = event.metadata.get("channel", event.metadata.get("channel_id"))
    return org.slack_rules.get(channel, SourceRule()) if channel in org.slack_channels and event.metadata.get("team_id") == org.slack_team_id else None


def source_allowed(event, config):
    rule = source_rule(event, config)
    if rule is None or not rule.enabled:
        return False
    if event.source == "github" and len(set(rule.event_types)) < 4 and event.kind.split('.')[0] not in rule.event_types:
        return False
    text = event.text.casefold()
    return (not rule.include_keywords or any(k.casefold() in text for k in rule.include_keywords)) and not any(k.casefold() in text for k in rule.exclude_keywords)


def resolve_routes(event: Event, config: AppConfig) -> list[Route]:
    org = next((org for org in config.orgs if org.id == event.org_id), None)
    if org is None:
        return []
    if not source_allowed(event, config):
        return []
    rule = source_rule(event, config)
    threshold = org.threshold if rule.threshold is None else rule.threshold
    if event.source == "github":
        if not event.repo:
            return []
        repo = next(
            (value for key, value in org.repos.items() if key.casefold() == event.repo.casefold()),
            None,
        )
        if repo is None:
            return []
        if repo.threshold is not None:
            threshold = repo.threshold
        recipients = (repo.recipients or [org.recipient]) if repo.override_recipients else ([org.recipient] if org.type == "startup" else repo.recipients)
    else:
        channel = event.metadata.get("channel", event.metadata.get("channel_id"))
        if event.metadata.get("team_id") != org.slack_team_id or channel not in org.slack_channels:
            return []
        recipients = rule.recipients or [org.recipient]
    return [
        Route(recipient=recipient, threshold=threshold)
        for recipient in dict.fromkeys(recipients)
        if recipient
    ]
