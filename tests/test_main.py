import json
import os
import uuid
from collections.abc import Iterable, Iterator

import httpx
import pytest
from starlette.testclient import TestClient

import main

CHAT_HISTORY_KEY = "copilotstudio.microsoft.com/a2a/chathistory"


def new_context() -> str:
    return str(uuid.uuid4())


def copilot_studio_request(method: str, text: str, context_id: str) -> dict:
    # Same shape as the request in the Microsoft Simple-A2A-Sample test_agent.http
    return {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": method,
        "params": {
            "message": {
                "contextId": context_id,
                "kind": "message",
                "messageId": str(uuid.uuid4()),
                "metadata": {
                    CHAT_HISTORY_KEY: [
                        {
                            "HasValue": True,
                            "Value": [
                                {
                                    "From": "User",
                                    "Locale": "en-US",
                                    "Text": text,
                                    "Timestamp": "2026-01-01T12:00:00+00:00",
                                }
                            ],
                        }
                    ]
                },
                "parts": [{"kind": "text", "text": text}],
                "role": "user",
            }
        },
    }


def sse_events(lines: Iterable[str]) -> list[dict]:
    return [json.loads(line[5:]) for line in lines if line.startswith("data:")]


def reply_text(message: dict) -> str:
    return "".join(part["text"] for part in message["parts"] if part["kind"] == "text")


@pytest.fixture(params=["local", pytest.param("remote", marks=pytest.mark.e2e)])
def http(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator:
    if request.param == "local":
        monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")
        main.conversations.clear()
        yield TestClient(main.build_app())
        return
    url = os.getenv("A2A_E2E_URL", "").rstrip("/")
    if not url:
        pytest.skip("A2A_E2E_URL not set")
    with httpx.Client(base_url=url, timeout=30) as remote:
        yield remote


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in ("PUBLIC_BASE_URL", "CONTAINER_APP_NAME", "CONTAINER_APP_ENV_DNS_SUFFIX", "PORT"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.mark.parametrize("path", ["/.well-known/agent-card.json", "/.well-known/agent.json"])
def test_agent_card(http: httpx.Client, path: str) -> None:
    response = http.get(path)
    assert response.status_code == 200
    card = response.json()
    assert card["protocolVersion"] == "0.3.0"
    assert card["preferredTransport"] == "JSONRPC"
    assert card["capabilities"]["streaming"] is True
    assert card["url"] == str(http.base_url).rstrip("/") + "/"
    assert 30 <= len(card["description"]) <= 1000
    assert card["skills"]


def test_message_send_replies_with_agent_message(http: httpx.Client) -> None:
    context_id = new_context()
    response = http.post("/", json=copilot_studio_request("message/send", "ping", context_id))
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    result = body["result"]
    assert result["kind"] == "message"
    assert result["role"] == "agent"
    assert result["contextId"] == context_id
    assert 'You said: "ping"' in reply_text(result)


def test_message_stream_ends_with_agent_message(http: httpx.Client) -> None:
    context_id = new_context()
    request = copilot_studio_request("message/stream", "ping", context_id)
    with http.stream("POST", "/", json=request) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response.iter_lines())
    assert events
    result = events[-1]["result"]
    assert result["kind"] == "message"
    assert result["role"] == "agent"
    assert result["contextId"] == context_id
    assert 'You said: "ping"' in reply_text(result)


def test_context_is_remembered_per_conversation(http: httpx.Client) -> None:
    context_id = new_context()
    first = http.post("/", json=copilot_studio_request("message/send", "ping", context_id))
    second = http.post("/", json=copilot_studio_request("message/send", "pong", context_id))
    other = http.post("/", json=copilot_studio_request("message/send", "ping", new_context()))
    assert "remember 1 message from" in reply_text(first.json()["result"])
    assert "remember 2 messages from" in reply_text(second.json()["result"])
    assert "remember 1 message from" in reply_text(other.json()["result"])


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b"not json", -32700),
        (b'{"foo": "bar"}', -32600),
        (b'{"jsonrpc": "2.0", "id": 1, "method": "nope/nope", "params": {}}', -32601),
    ],
)
def test_bad_requests_return_jsonrpc_error(http: httpx.Client, body: bytes, code: int) -> None:
    response = http.post("/", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 200
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello", "Hello!"),
        ("Hi there", "Hello!"),
        ("what can you do?", "generic A2A test agent"),
        ("", "didn't receive any text"),
        ("   ", "didn't receive any text"),
        ("ping", 'You said: "ping"'),
    ],
)
def test_respond(text: str, expected: str) -> None:
    assert expected in main.respond([], text)


def test_public_url_defaults_to_localhost(clean_env: pytest.MonkeyPatch) -> None:
    assert main.public_url() == "http://localhost:2009/"


def test_public_url_uses_container_apps_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CONTAINER_APP_NAME", "my-agent")
    clean_env.setenv("CONTAINER_APP_ENV_DNS_SUFFIX", "westeurope.azurecontainerapps.io")
    assert main.public_url() == "https://my-agent.westeurope.azurecontainerapps.io/"


def test_public_url_prefers_explicit_setting(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("CONTAINER_APP_NAME", "my-agent")
    clean_env.setenv("CONTAINER_APP_ENV_DNS_SUFFIX", "westeurope.azurecontainerapps.io")
    clean_env.setenv("PUBLIC_BASE_URL", "https://agents.example.com")
    assert main.public_url() == "https://agents.example.com/"


def test_request_log_shows_method_and_context() -> None:
    messages: list[str] = []
    sink_id = main.logger.add(messages.append, level="INFO", format="{message}")
    try:
        context_id = new_context()
        client = TestClient(main.build_app())
        client.post("/", json=copilot_studio_request("message/send", "ping", context_id))
    finally:
        main.logger.remove(sink_id)
    assert any(f"method=message/send contextId={context_id}" in line for line in messages)
