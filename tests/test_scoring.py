import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from triage.config import Settings
from triage.models import Event
from triage.scoring import RelevanceOutput, Scorer, _event_input


def event(**overrides):
    values = dict(
        id="github:1",
        org_id="acme",
        source="github",
        kind="pull_request",
        subject_key="pr:1",
        text="Please review this production fix",
    )
    return Event(**(values | overrides))


@pytest.mark.asyncio
async def test_sdk_is_tool_free_private_bounded_and_usage_is_trusted(monkeypatch):
    run = AsyncMock(
        return_value=SimpleNamespace(
            final_output=RelevanceOutput(
                score=0.85, rationale="Review requested", summary="Fix needs review"
            ),
            context_wrapper=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=72, output_tokens=31)
            ),
        )
    )
    monkeypatch.setattr("triage.scoring.Runner.run", run)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.example")
    scorer = Scorer(Settings(openai_api_key="unit-test-key"))
    try:
        score = await scorer.score(event())
        assert (score.score, score.input_tokens, score.output_tokens, score.fallback) == (
            0.85,
            72,
            31,
            False,
        )
        agent = run.call_args.args[0]
        assert agent.tools == [] and agent.handoffs == []
        assert agent.output_type is RelevanceOutput
        assert agent.model_settings.max_tokens == 650
        assert agent.model_settings.store is False
        assert scorer._client.max_retries == 0
        assert str(scorer._client.base_url) == "https://api.openai.com/v1/"
        options = run.call_args.kwargs
        assert options["max_turns"] == 1
        assert options["run_config"].tracing_disabled
        assert not options["run_config"].trace_include_sensitive_data
        assert "UNTRUSTED DATA" in agent.instructions
        assert "0.90-1.00" in agent.instructions and "0.00-0.39" in agent.instructions
    finally:
        await scorer.close()


def test_only_bounded_event_fields_reach_provider():
    payload = _event_input(
        event(text="x" * 20000, repo="r" * 1000, metadata={"secret": "NEVER_SEND"})
    )
    data = json.loads(payload)["untrusted_event"]
    assert len(data["text"]) == 12000 and len(data["repo"]) == 300
    assert data["truncated"] is True
    assert "NEVER_SEND" not in payload
    assert set(data) == {"source", "kind", "repo", "actor", "text", "truncated"}


@pytest.mark.parametrize("score", [-0.1, 1.1, float("nan"), float("inf")])
def test_out_of_range_output_rejected(score):
    with pytest.raises(ValidationError):
        RelevanceOutput(score=score, rationale="why", summary="what")


@pytest.mark.asyncio
async def test_missing_credentials_and_provider_failure_propagate(monkeypatch):
    run = AsyncMock(side_effect=RuntimeError("provider unavailable"))
    monkeypatch.setattr("triage.scoring.Runner.run", run)
    with pytest.raises(RuntimeError, match="not configured"):
        await Scorer(Settings()).score(event())
    run.assert_not_called()
    scorer = Scorer(Settings(openai_api_key="unit-test-key"))
    try:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await scorer.score(event())
        assert run.await_count == 1
    finally:
        await scorer.close()
