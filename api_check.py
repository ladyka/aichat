#!/usr/bin/env python3
"""Quick check for aichat API token against a host."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def request(
    method: str,
    url: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict | str]:
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Check aichat API on a remote host")
    parser.add_argument(
        "--host",
        default=os.environ.get("AICHAT_HOST", "http://127.0.0.1:20000"),
        help="Base URL, e.g. https://aichat.example.com (or AICHAT_HOST)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("AICHAT_TOKEN"),
        help="API token aichat_… (or AICHAT_TOKEN)",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("AICHAT_MODEL", "default"),
        help="Model id from /v1/models (default: default)",
    )
    parser.add_argument(
        "--message",
        default="Привет! Ответь одним словом.",
        help="Test prompt",
    )
    args = parser.parse_args()

    if not args.token:
        print("error: pass --token or set AICHAT_TOKEN", file=sys.stderr)
        return 1

    host = args.host.rstrip("/")
    print(f"Host:  {host}")
    print(f"Token: {args.token[:16]}…")
    print()

    print("1) GET /v1/models")
    status, models = request("GET", f"{host}/v1/models")
    print(f"   status: {status}")
    if status == 200 and isinstance(models, dict):
        ids = [m.get("id") for m in models.get("data", []) if isinstance(m, dict)]
        print(f"   models: {len(ids)}")
        if ids:
            print(f"   first:  {ids[0]}")
    else:
        print(f"   body:   {models}")
        return 1

    print()
    print("2) POST /v1/chat/completions")
    payload = {
        "model": args.model,
        "stream": False,
        "messages": [{"role": "user", "content": args.message}],
    }
    status, data = request(
        "POST",
        f"{host}/v1/chat/completions",
        token=args.token,
        body=payload,
        timeout=120.0,
    )
    print(f"   status: {status}")
    if status != 200:
        print(f"   body:   {data}")
        return 1

    if not isinstance(data, dict):
        print(f"   body:   {data}")
        return 1

    model = data.get("model")
    content = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    usage = data.get("usage", {})
    print(f"   model:   {model}")
    print(f"   reply:   {content!r}")
    print(f"   usage:   {usage}")
    print()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
