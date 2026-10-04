import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from flask import Flask, request
from google.api_core.exceptions import ServiceUnavailable
from judge_relay import Rejected, Relay, Settings, reserve_state


class Store:
    """Serializable fake store; the production equivalent is a Firestore transaction."""

    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()

    def reserve(self, settings):
        with self.lock:
            self.state = reserve_state(self.state, settings, time.time())


@pytest.fixture
def settings():
    return Settings(
        enabled=True,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        total_calls=700,
        calls_per_minute=60,
        max_body_bytes=32768,
        max_output_tokens=2048,
        openai_model="gpt-4o-mini",
        typesafe_model="jev-1.13.0",
        upstream_timeout_s=45,
        project_id="test",
        database="test",
    )


@pytest.fixture
def setup(settings, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "private-openai-credential")
    monkeypatch.setenv("TYPESAFE_API_KEY", "private-typesafe-credential")
    calls = []
    replies = []

    def provider(req):
        calls.append(req)
        return replies.pop(0) if replies else httpx.Response(200, json={"choices": []})

    store = Store()
    relay = Relay(settings, store, httpx.Client(transport=httpx.MockTransport(provider)))
    app = Flask(__name__)

    @app.route("/", defaults={"path": ""}, methods=["GET", "POST", "DELETE"])
    @app.route("/<path:path>", methods=["GET", "POST", "DELETE"])
    def handle(path):
        return relay.handle(request)

    return app.test_client(), calls, replies, store


def chat(**extra):
    return {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Hello"}],
        **extra,
    }


def test_chat_replaces_caller_auth_and_bounds_default_output(setup):
    client, calls, _, store = setup
    response = client.post(
        "/openai/v1/chat/completions",
        json=chat(),
        headers={
            "Authorization": "Bearer attacker",
            "X-Forward-To": "https://attacker.test",
        },
    )
    assert response.status_code == 200
    sent = calls[0]
    assert str(sent.url) == "https://api.openai.com/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer private-openai-credential"
    assert "x-forward-to" not in sent.headers
    assert json.loads(sent.content)["max_tokens"] == 2048
    assert store.state["total_calls"] == 1
    assert b"private-" not in response.data


def test_typesafe_uses_its_own_private_key(setup):
    client, calls, replies, _ = setup
    replies.append(httpx.Response(200, json={"answers": {"risky": {"noul": 0.05}}}))
    response = client.post(
        "/typesafe/v1/systemone",
        json={
            "model": "jev-1.13.0",
            "state": {"checkpoint": "input", "text": "hello", "context": "user"},
            "questions": {
                "risky": {
                    "type": "noul",
                    "instructions": "Is it risky?",
                    "criteria": {"true": "Risky", "false": "Safe"},
                },
                "category": {
                    "type": "choice",
                    "instructions": "Pick category",
                    "criteria": {"benign": "Safe", "prompt_injection": "Risky"},
                },
            },
        },
    )
    assert response.status_code == 200
    assert response.json["answers"]["risky"]["noul"] == 0.05
    assert str(calls[0].url) == "https://api.typesafe.ai/v1/systemone"
    assert calls[0].headers["authorization"] == "Bearer private-typesafe-credential"


@pytest.mark.parametrize(
    "body",
    [
        chat(model="expensive-model"),
        chat(stream=True),
        chat(n=5),
        chat(max_tokens=2049),
        chat(max_completion_tokens=2049),
        chat(max_tokens=1, max_completion_tokens=1),
        chat(messages=[]),
        chat(upstream_url="https://attacker.test"),
    ],
)
def test_rejected_payload_never_reserves_or_forwards(setup, body):
    client, calls, _, store = setup
    assert client.post("/openai/v1/chat/completions", json=body).status_code == 400
    assert not calls and not store.state


@pytest.mark.parametrize(
    "path,method,status",
    [
        ("/secrets", "GET", 404),
        ("/openai/v1/chat/completions", "GET", 405),
        ("/openai/v1/chat/completions?url=evil", "POST", 400),
    ],
)
def test_fixed_routes(setup, path, method, status):
    client, calls, _, _ = setup
    assert client.open(path, method=method, json=chat()).status_code == status
    assert not calls


def test_body_limit(setup):
    client, calls, _, _ = setup
    response = client.post("/openai/v1/chat/completions", json=chat(user="x" * 32768))
    assert response.status_code == 413 and not calls


def test_provider_error_is_sanitized_and_consumes_reservation(setup):
    client, calls, replies, store = setup
    replies.append(httpx.Response(401, json={"error": "private-openai-credential"}))
    response = client.post("/openai/v1/chat/completions", json=chat())
    assert response.status_code == 502
    assert b"private-" not in response.data
    assert len(calls) == 1 and store.state["total_calls"] == 1


def test_credential_reflection_is_refused(setup):
    client, _, replies, _ = setup
    replies.append(httpx.Response(200, json={"content": "private-typesafe-credential"}))
    response = client.post("/openai/v1/chat/completions", json=chat())
    assert response.status_code == 502 and b"private-" not in response.data


def test_missing_secret_never_reserves(setup, monkeypatch):
    client, calls, _, store = setup
    monkeypatch.delenv("OPENAI_API_KEY")
    assert client.post("/openai/v1/chat/completions", json=chat()).status_code == 503
    assert not calls and not store.state


def test_static_health_and_models_make_no_provider_calls(setup):
    client, calls, _, store = setup
    assert client.get("/health").status_code == 200
    assert client.get("/openai/v1/models").json["data"][0]["id"] == "gpt-4o-mini"
    assert not calls and not store.state


def test_expiry_and_disabled_never_forward(setup, settings):
    client, calls, _, store = setup
    settings.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.post("/openai/v1/chat/completions", json=chat()).status_code == 410
    settings.enabled = False
    assert client.post("/openai/v1/chat/completions", json=chat()).status_code == 503
    assert not calls and not store.state


def test_total_and_minute_quota_persist_between_requests(setup, settings):
    client, calls, _, store = setup
    settings.total_calls = 2
    for _ in range(2):
        assert client.post("/openai/v1/chat/completions", json=chat()).status_code == 200
    assert client.post("/openai/v1/chat/completions", json=chat()).status_code == 429
    assert len(calls) == 2
    state = store.state.copy()
    with pytest.raises(Rejected):
        reserve_state(state, settings, time.time() + 120)


def test_concurrent_reservations_stop_at_global_allowance(settings):
    settings.total_calls = 7
    store = Store()

    def reserve(_):
        try:
            store.reserve(settings)
            return True
        except Rejected:
            return False

    with ThreadPoolExecutor(max_workers=16) as executor:
        outcomes = list(executor.map(reserve, range(80)))
    assert sum(outcomes) == 7 and store.state["total_calls"] == 7


def test_minute_window_resets_without_refunding_lifetime(settings):
    settings.calls_per_minute = 1
    state = reserve_state({}, settings, 60)
    with pytest.raises(Rejected, match="minute"):
        reserve_state(state, settings, 61)
    assert reserve_state(state, settings, 120)["total_calls"] == 2


def test_storage_unavailable_fails_closed(setup, monkeypatch):
    client, calls, _, store = setup

    def fail(settings):
        raise ServiceUnavailable("private database details")

    monkeypatch.setattr(store, "reserve", fail)
    response = client.post("/openai/v1/chat/completions", json=chat())
    assert response.status_code == 503 and not calls
    assert b"private" not in response.data
