"""Check that the NVIDIA API key in .env works. Never prints the key.

Usage:  py -3.13 scripts/check_nvidia_key.py
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> dict:
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("NVIDIA_", "NEMOTRON_"))})
    return env


def main() -> int:
    env = load_env()
    key = env.get("NVIDIA_API_KEY", "")
    base = env.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
    model = env.get("NEMOTRON_FAST_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")
    if not key or "REPLACE_ME" in key:
        print("FAIL: NVIDIA_API_KEY missing in .env (copy .env.example to .env and paste your nvapi-... key)")
        return 1
    print(f"key loaded: {key[:6]}…({len(key)} chars)  model: {model}")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly: DEALPILOT OK"}],
        "max_tokens": 32,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"FAIL: HTTP {e.code} — {e.read()[:300].decode(errors='replace')}")
        return 1
    except Exception as e:  # network etc.
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    msg = data["choices"][0]["message"]
    print("OK: key works. Reply:", (msg.get("content") or msg.get("reasoning_content") or "").strip()[:120])
    return 0


if __name__ == "__main__":
    sys.exit(main())
