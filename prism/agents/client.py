"""The shared Nemotron client (NEM-01): modes, .env, urllib transport, digest, cache, retries, ChatClient.

Modes (env ``NEMOTRON_MODE``): ``off`` makes no call; ``replay`` reads the record/replay cache only; ``live``
reads the cache first and calls the hosted model only on a miss. ``live`` without a usable key is ``off``.

The key is read only through ``env()``, appears only inside the Authorization header and is never logged,
cached or put in an error message. Model ids come from env only. Wire format: OpenAI chat-completions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import urllib.error
import urllib.request  # call urlopen through the module attribute: tests patch urllib.request.urlopen
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from prism.engine.config import REPO_ROOT

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
_ENV_PREFIXES = ("NVIDIA_", "NEMOTRON_", "PRISM_NEMOTRON_", "PRISM_ENV_", "PRISM_AGENT_")

# ---------------------------------------------------------------- environment

_ENV_CACHE: dict[tuple[str, int], dict[str, str]] = {}


def _env_file() -> Path:
    override = os.environ.get("PRISM_ENV_FILE")
    return Path(override) if override else REPO_ROOT / ".env"


def _dotenv() -> dict[str, str]:
    p = _env_file()
    try:
        key = (str(p), p.stat().st_mtime_ns)
    except OSError:
        return {}
    if key in _ENV_CACHE:
        return _ENV_CACHE[key]
    env: dict[str, str] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                if k.startswith(_ENV_PREFIXES):
                    env[k] = v.strip().strip('"').strip("'")
    except (OSError, UnicodeDecodeError):
        env = {}
    _ENV_CACHE.clear()
    _ENV_CACHE[key] = env
    return env


def env(name: str) -> str:
    """Process env first, then the repo .env. Read at call time, never at import."""
    v = os.environ.get(name)
    if v is None or not v.strip():
        v = _dotenv().get(name, "")
    return (v or "").strip()


def key_usable() -> bool:
    key = env("NVIDIA_API_KEY")
    return bool(key) and "REPLACE_ME" not in key


def mode() -> str:
    """``off`` | ``replay`` | ``live`` (N9)."""
    m = env("NEMOTRON_MODE").casefold()
    if m == "off":
        return "off"
    if m == "replay":
        return "replay"
    if m in ("", "live"):
        return "live" if key_usable() else "off"
    return "off"


# ---------------------------------------------------------------- transport (the only network code)

class NemotronError(Exception):
    """A failure reduced to a short message with no headers and no key."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def post_chat(model: str, messages: list, *, timeout: float, max_tokens: int,
              tools: Optional[list] = None, tool_choice: Any = None) -> dict:
    """POST one chat completion; return the assistant message dict."""
    key = env("NVIDIA_API_KEY")
    base = (env("NVIDIA_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    payload: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": 0}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice if tool_choice is not None else "auto"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https base from env
            data = json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        code = int(getattr(e, "code", 0) or 0)
        raise NemotronError(f"http {code}", retryable=code == 429 or code >= 500) from None
    except TimeoutError:
        raise NemotronError("timeout", retryable=True) from None
    except (urllib.error.URLError, OSError) as e:
        raise NemotronError(f"network {type(e).__name__}", retryable=True) from None
    except Exception as e:  # noqa: BLE001
        raise NemotronError(f"bad response {type(e).__name__}") from None
    try:
        message = data["choices"][0]["message"]
    except Exception:  # noqa: BLE001
        raise NemotronError("no reply") from None
    if not isinstance(message, dict):
        raise NemotronError("no reply")
    return message


def text_reply(message: Any) -> str:
    """The content string of an assistant message (old vl._transport semantics)."""
    try:
        content = message["content"]
    except Exception:  # noqa: BLE001
        raise NemotronError("no reply") from None
    if not isinstance(content, str) or not content.strip():
        raise NemotronError("empty reply")
    return content


# ---------------------------------------------------------------- digest and cache

def digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        b = p if isinstance(p, (bytes, bytearray)) else str(p).encode("utf-8")
        h.update(str(len(b)).encode("ascii") + b":" + bytes(b))
    return h.hexdigest()


def cache_dir(default: str) -> Path:
    override = env("PRISM_NEMOTRON_CACHE_DIR")
    if override:
        return Path(override)
    d = Path(str(default))
    return d if d.is_absolute() else REPO_ROOT / d


def cache_read(directory: Path, key: str) -> Optional[dict]:
    try:
        rec = json.loads((Path(directory) / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ans = rec.get("answer") if isinstance(rec, dict) else None
    return ans if isinstance(ans, dict) else None


def cache_write(directory: Path, key: str, record: dict) -> None:
    try:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_text(json.dumps(record, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def call_with_retries(send: Callable[[], Any], *, retries: int, sleeps: Sequence[float],
                      budget_left: Callable[[], bool], spend: Callable[[], None],
                      sleep: Callable[[float], None]) -> Any:
    """Bounded retries; every attempt costs one budget unit. None on any failure, never raises."""
    sleeps = list(sleeps) or [0]
    for attempt in range(retries + 1):
        if not budget_left():
            return None
        spend()
        try:
            return send()
        except NemotronError as e:
            if e.retryable and attempt < retries:
                sleep(float(sleeps[min(attempt, len(sleeps) - 1)]))
                continue
            return None
        except Exception:  # noqa: BLE001 - a transport that raises anything else is a failure, not a crash
            return None
    return None


def agent_cache_dir() -> Path:
    """Committed replay root for agents (env PRISM_AGENT_CACHE_DIR overrides)."""
    override = env("PRISM_AGENT_CACHE_DIR")
    return Path(override) if override else REPO_ROOT / "eval" / "nemotron_replay"


def agents_dir() -> Path:
    """Where agent outputs are written and served from (env PRISM_AGENTS_DIR overrides)."""
    override = env("PRISM_AGENTS_DIR")
    return Path(override) if override else REPO_ROOT / "out" / "agents"


# ---------------------------------------------------------------- ChatClient

def _normalize(message: Any) -> dict:
    content = message.get("content") if isinstance(message, dict) else None
    out: dict[str, Any] = {"content": content if isinstance(content, str) else "", "tool_calls": []}
    raw = message.get("tool_calls") if isinstance(message, dict) else None
    for i, tc in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        args = fn.get("arguments")
        if isinstance(args, dict):
            args = json.dumps(args, sort_keys=True)
        if not isinstance(args, str):
            args = "{}"
        cid = tc.get("id")
        out["tool_calls"].append({"id": cid if isinstance(cid, str) and cid else f"call_{i}",
                                  "name": name, "arguments": args})
    return out


class ChatClient:
    """Tool-calling chat in the OpenAI chat-completions format with record/replay (MODEL_DESIGN N8)."""

    def __init__(self, model_env: str = "NEMOTRON_AGENT_MODEL", *, cache_subdir: str = "", max_calls: int = 40,
                 timeout: float = 60.0, max_tokens: int = 900, retries: int = 2, sleeps: Sequence[float] = (2, 5),
                 transport: Optional[Callable[..., Any]] = None, sleep: Optional[Callable[[float], None]] = None):
        self.model_env = model_env
        self.cache_subdir = cache_subdir
        self.max_calls = max_calls
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.retries = retries
        self.sleeps = tuple(sleeps)
        self._transport = transport
        self._sleep = sleep
        self.calls = 0
        self.hits = 0
        self.misses = 0
        self._failed: set[str] = set()

    @property
    def mode(self) -> str:
        return mode()

    @property
    def model(self) -> str:
        return env(self.model_env)

    def _dir(self) -> Path:
        base = agent_cache_dir()
        return base / self.cache_subdir if self.cache_subdir else base

    def _key(self, model: str, messages: list, tools: Optional[list]) -> str:
        return digest(model, json.dumps(messages, sort_keys=True, ensure_ascii=False),
                      json.dumps(tools or [], sort_keys=True),
                      json.dumps({"temperature": 0, "max_tokens": self.max_tokens}, sort_keys=True))

    def chat(self, messages: list, tools: Optional[list] = None) -> Optional[dict]:
        try:
            how = mode()
            model = self.model
            if how == "off" or not model:
                return None
            key = self._key(model, messages, tools)
            d = self._dir()
            cached = cache_read(d, key)
            if cached is not None:
                self.hits += 1
                return copy.deepcopy(cached)
            if how != "live":
                self.misses += 1
                return None
            if key in self._failed:
                return None

            def send() -> Any:
                fn = self._transport if self._transport is not None else post_chat
                return fn(model, messages, timeout=self.timeout, max_tokens=self.max_tokens,
                          tools=tools or None, tool_choice="auto" if tools else None)

            def spend() -> None:
                self.calls += 1

            def nap(s: float) -> None:
                (self._sleep if self._sleep is not None else _default_sleep)(s)

            reply = call_with_retries(send, retries=self.retries, sleeps=self.sleeps,
                                      budget_left=lambda: self.calls < self.max_calls, spend=spend, sleep=nap)
            if not isinstance(reply, dict):
                self._failed.add(key)
                return None
            answer = _normalize(reply)
            cache_write(d, key, {"model": model, "answer": answer})
            return copy.deepcopy(answer)
        except Exception:  # noqa: BLE001
            return None


def _default_sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def assistant_message(answer: dict) -> dict:
    """OpenAI-format assistant message for the next turn."""
    msg: dict[str, Any] = {"role": "assistant", "content": (answer or {}).get("content") or ""}
    calls = (answer or {}).get("tool_calls") or []
    if calls:
        msg["tool_calls"] = [{"id": c["id"], "type": "function",
                              "function": {"name": c["name"], "arguments": c.get("arguments") or "{}"}}
                             for c in calls]
    return msg


def tool_message(call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}
