from types import SimpleNamespace

import httpx
import pytest

from triage.delivery import DeliveryError, SlackDelivery, _payload


def batch(count=1):
    return {
        "id": "batch-1",
        "org_id": "org",
        "recipient": "C123",
        "events": [{"url": "https://github.com/org/repo/pull/1"} for _ in range(count)],
        "scores": [
            {
                "score": 0.9,
                "summary": "<@U123> <!channel> urgent",
                "rationale": "A release blocker",
                "fallback": False,
            }
            for _ in range(count)
        ],
    }


async def delivery(handler, token="secret"):
    client = SlackDelivery(SimpleNamespace(slack_bot_token=token))
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_success_and_safe_payload():
    def handler(request):
        assert request.url == "https://slack.com/api/chat.postMessage"
        assert request.headers["authorization"] == "Bearer secret"
        return httpx.Response(200, json={"ok": True, "ts": "123.456"})

    client = await delivery(handler)
    try:
        assert await client.send(batch()) == "123.456"
    finally:
        await client.close()
    payload = _payload(batch())
    assert "<@" not in payload["text"] and "<!channel>" not in payload["text"]
    assert payload["parse"] == "none"
    assert payload["link_names"] is False
    assert payload["unfurl_links"] is False and payload["unfurl_media"] is False
    assert payload["blocks"][1]["text"]["type"] == "plain_text"
    assert payload["blocks"][1]["accessory"]["url"].startswith("https://github.com/")
    assert "Why:" in payload["blocks"][1]["text"]["text"]
    assert payload["metadata"] == _payload(batch())["metadata"]


def test_large_batch_is_bounded_and_omissions_visible():
    data = batch(100)
    for score in data["scores"]:
        score["summary"] = "X" * 10000
        score["rationale"] = "Y" * 10000
    payload = _payload(data)
    assert len(payload["text"]) < 4000
    assert len(payload["blocks"]) <= 50
    assert "80 lower-ranked" in payload["blocks"][-1]["elements"][0]["text"]
    assert all(len(block.get("text", {}).get("text", "")) <= 3000 for block in payload["blocks"])


@pytest.mark.parametrize(
    "status,data,retryable,uncertain",
    [
        (429, {}, True, False),
        (503, {}, False, True),
        (408, {}, False, True),
        (401, {}, False, False),
        (200, {"ok": True}, False, True),
        (200, {"ok": False, "error": "channel_not_found"}, False, False),
        (200, {"ok": False, "error": "internal_error"}, False, True),
        (200, {}, False, True),
        (200, [], False, True),
    ],
)
async def test_http_classification(status, data, retryable, uncertain):
    client = await delivery(
        lambda request: httpx.Response(status, json=data, headers={"Retry-After": "42"})
    )
    try:
        with pytest.raises(DeliveryError) as caught:
            await client.send(batch())
        assert caught.value.retryable is retryable
        assert caught.value.uncertain is uncertain
        if status == 429:
            assert caught.value.retry_after == 42
    finally:
        await client.close()


@pytest.mark.parametrize(
    "error,retryable,uncertain",
    [
        (httpx.ConnectError, True, False),
        (httpx.ConnectTimeout, True, False),
        (httpx.ReadTimeout, False, True),
        (httpx.WriteError, False, True),
        (httpx.RemoteProtocolError, False, True),
    ],
)
async def test_transport_classification(error, retryable, uncertain):
    calls = []

    def handler(request):
        calls.append(request)
        raise error("secret body must not leak")

    client = await delivery(handler)
    try:
        with pytest.raises(DeliveryError) as caught:
            await client.send(batch())
        assert caught.value.retryable is retryable
        assert caught.value.uncertain is uncertain
        assert "secret body" not in str(caught.value)
        assert len(calls) == 1
    finally:
        await client.close()


async def test_missing_token_does_not_send():
    def handler(request):
        pytest.fail("Request must not be sent")

    client = await delivery(handler, token="")
    try:
        with pytest.raises(DeliveryError, match="SLACK_BOT_TOKEN"):
            await client.send(batch())
    finally:
        await client.close()


async def test_invalid_json_is_uncertain():
    client = await delivery(lambda request: httpx.Response(200, text="invalid"))
    try:
        with pytest.raises(DeliveryError) as caught:
            await client.send(batch())
        assert caught.value.uncertain
    finally:
        await client.close()


def test_non_https_link_is_removed():
    data = batch()
    data["events"][0]["url"] = "javascript:alert(1)"
    assert "accessory" not in _payload(data)["blocks"][1]

@pytest.mark.parametrize('code', ['messages_tab_disabled', 'missing_scope', 'channel_not_found', 'secret-response-content'])
async def test_safe_diagnostic_codes(code):
    client = await delivery(lambda request: httpx.Response(200, json={'ok': False, 'error': code}))
    try:
        with pytest.raises(DeliveryError) as caught:
            await client.send(batch())
        assert caught.value.code == (None if code == 'secret-response-content' else code)
        assert 'secret-response-content' not in str(caught.value)
    finally:
        await client.close()
