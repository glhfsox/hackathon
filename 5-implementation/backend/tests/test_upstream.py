import httpx
import pytest
import respx

from app.core.upstream import HttpUpstream
from app.protocols.upstream import UpstreamError

KEY_ENV = "TEST_UPSTREAM_KEY"
BASE = "https://llm.test/v1"
URL = f"{BASE}/chat/completions"
REPLY = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def mock():
    with respx.mock(assert_all_called=False) as router:
        yield router


async def test_sends_the_key_from_the_named_env_var(http, mock, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    route = mock.post(URL).respond(200, json=REPLY)

    await HttpUpstream(http).chat({}, base_url=BASE, timeout_s=1, api_key_env=KEY_ENV)

    assert route.calls.last.request.headers["authorization"] == "Bearer sk-test"


async def test_no_key_env_sends_no_authorization(http, mock):
    route = mock.post(URL).respond(200, json=REPLY)

    await HttpUpstream(http).chat({}, base_url=BASE, timeout_s=1)

    assert "authorization" not in route.calls.last.request.headers


async def test_empty_key_env_fails_without_calling_the_model(http, mock, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    route = mock.post(URL).respond(200, json=REPLY)

    with pytest.raises(UpstreamError, match=KEY_ENV) as exc:
        await HttpUpstream(http).chat({}, base_url=BASE, timeout_s=1, api_key_env=KEY_ENV)

    assert not route.called
    assert "sk-" not in str(exc.value)
