"""Resolve only destinations explicitly authorized by current configuration."""

from triage.config import AppConfig
from triage.models import Event, Route


def resolve_routes(event: Event, config: AppConfig) -> list[Route]:
    org = next((org for org in config.orgs if org.id == event.org_id), None)
    if org is None:
        return []
    threshold = org.threshold
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
        recipients = [org.recipient] if org.type == "startup" else repo.recipients
    else:
        channel = event.metadata.get("channel", event.metadata.get("channel_id"))
        if event.metadata.get("team_id") != org.slack_team_id or channel not in org.slack_channels:
            return []
        recipients = [org.recipient]
    return [
        Route(recipient=recipient, threshold=threshold)
        for recipient in dict.fromkeys(recipients)
        if recipient
    ]
