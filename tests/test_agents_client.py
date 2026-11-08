"""Shared Nemotron client (06-01): ChatClient record/replay, off, budget, retries, tool calls, key hygiene."""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from pathlib import Path

import pytest

from prism.agents import client

SENTINEL = "nvapi-SENTINEL-do-not-leak-123"
MODEL = "fake/agent-model"
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "hello"}]
TOOLS = [{"type": "function", "function": {"name": "t", "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}}]


class FakeTransport:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, model, messages, **kw):
        self.calls.append((model, messages, kw))
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, BaseException):
            raise r
        return r


def live(monkeypatch):
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    monkeypatch.setenv("NVIDIA_API_KEY", SENTINEL)
    monkeypatch.setenv("NEMOTRON_AGENT_MODEL", MODEL)


REPLY = {"role": "assistant", "content": "thinking",
         "tool_calls": [{"id": "abc", "type": "function", "function": {"name": "t", "arguments": "{\"x\": 1}"}}]}


def cache_files():
    return list(client.agent_cache_dir().rglob("*.json"))


def test_record_then_replay(monkeypatch):
    live(monkeypatch)
    fake = FakeTransport(REPLY)
    c = client.ChatClient(cache_subdir="unit", transport=fake, sleep=lambda s: None)
    ans = c.chat(MSGS, TOOLS)
    assert ans == {"content": "thinking", "tool_calls": [{"id": "abc", "name": "t", "arguments": "{\"x\": 1}"}]}
    assert len(fake.calls) == 1 and c.calls == 1
    files = cache_files()
    assert len(files) == 1 and files[0].parent.name == "unit"
    assert SENTINEL not in files[0].read_text(encoding="utf-8")
    assert fake.calls[0][2]["tool_choice"] == "auto"

    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    fake2 = FakeTransport(RuntimeError("no network in replay"))
    c2 = client.ChatClient(cache_subdir="unit", transport=fake2)
    assert c2.chat(MSGS, TOOLS) == ans and fake2.calls == [] and c2.hits == 1


def test_off_miss_and_no_model(monkeypatch):
    live(monkeypatch)
    fake = FakeTransport(REPLY)
    monkeypatch.setenv("NEMOTRON_MODE", "off")
    assert client.ChatClient(transport=fake).chat(MSGS, TOOLS) is None
    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    c = client.ChatClient(transport=fake)
    assert c.chat([{"role": "user", "content": "other"}], TOOLS) is None and c.misses == 1
    monkeypatch.setenv("NEMOTRON_MODE", "live")
    monkeypatch.delenv("NEMOTRON_AGENT_MODEL")
    assert client.ChatClient(transport=fake).chat(MSGS, TOOLS) is None
    assert fake.calls == []


def test_key_includes_tools_and_params(monkeypatch):
    live(monkeypatch)
    fake = FakeTransport(REPLY)
    client.ChatClient(transport=fake).chat(MSGS, TOOLS)
    monkeypatch.setenv("NEMOTRON_MODE", "replay")
    assert client.ChatClient(transport=fake).chat(MSGS, TOOLS) is not None
    assert client.ChatClient(transport=fake).chat(MSGS, None) is None
    assert client.ChatClient(transport=fake, max_tokens=901).chat(MSGS, TOOLS) is None


def test_retry_budget_and_failures(monkeypatch):
    live(monkeypatch)
    slept = []
    fake = FakeTransport(client.NemotronError("http 429", retryable=True), REPLY)
    c = client.ChatClient(transport=fake, sleep=slept.append)
    assert c.chat(MSGS) is not None and len(fake.calls) == 2 and slept == [2.0]

    bad = FakeTransport(client.NemotronError("http 400"))
    c = client.ChatClient(transport=bad, sleep=lambda s: None)
    assert c.chat([{"role": "user", "content": "q1"}]) is None and len(bad.calls) == 1
    assert c.chat([{"role": "user", "content": "q1"}]) is None and len(bad.calls) == 1  # failed key not re-asked

    boom = FakeTransport(RuntimeError("boom"))
    assert client.ChatClient(transport=boom).chat([{"role": "user", "content": "q2"}]) is None

    ok = FakeTransport(REPLY)
    c = client.ChatClient(transport=ok, max_calls=1)
    assert c.chat([{"role": "user", "content": "a"}]) is not None
    assert c.chat([{"role": "user", "content": "b"}]) is None and len(ok.calls) == 1


def test_tool_call_normalization_and_wire_messages(monkeypatch):
    live(monkeypatch)
    msg = {"content": None, "tool_calls": [
        {"function": {"name": "a", "arguments": "{}"}},
        {"id": "x", "function": {"arguments": "{}"}},  # no name: dropped
        "junk",
        {"id": "y", "function": {"name": "b"}},
    ]}
    ans = client.ChatClient(transport=FakeTransport(msg)).chat(MSGS, TOOLS)
    assert ans == {"content": "", "tool_calls": [{"id": "call_0", "name": "a", "arguments": "{}"},
                                                 {"id": "y", "name": "b", "arguments": "{}"}]}
    am = client.assistant_message(ans)
    assert am["role"] == "assistant" and am["tool_calls"][0] == {
        "id": "call_0", "type": "function", "function": {"name": "a", "arguments": "{}"}}
    assert client.tool_message("y", "res") == {"role": "tool", "tool_call_id": "y", "content": "res"}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_chat_body_and_http_error(monkeypatch, caplog):
    live(monkeypatch)
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _Resp(json.dumps({"choices": [{"message": {"role": "assistant", "content": "hi"}}]}).encode())

    monkeypatch.setattr(client.urllib.request, "urlopen", fake_urlopen)
    assert client.post_chat(MODEL, MSGS, timeout=1, max_tokens=5)["content"] == "hi"
    assert set(json.loads(seen[-1].data)) == {"model", "messages", "max_tokens", "temperature"}
    client.post_chat(MODEL, MSGS, timeout=1, max_tokens=5, tools=TOOLS, tool_choice="auto")
    assert set(json.loads(seen[-1].data)) == {"model", "messages", "max_tokens", "temperature", "tools", "tool_choice"}
    assert SENTINEL not in seen[-1].data.decode()

    def raise_401(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(client.urllib.request, "urlopen", raise_401)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(client.NemotronError) as e:
        client.post_chat(MODEL, MSGS, timeout=1, max_tokens=5)
    assert str(e.value) == "http 401" and SENTINEL not in str(e.value)
    assert SENTINEL not in caplog.text


def test_agent_dirs_isolated():
    assert "nemotron_replay" not in str(client.agent_cache_dir())
    assert Path(client.agents_dir()).name == "agents_out"
