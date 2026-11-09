"""Phase 0A LiteLLM /key/info feasibility probe.

This script creates synthetic LiteLLM virtual keys through an existing local
LiteLLM proxy, then probes /key/info and selected authorization behavior.
It intentionally redacts key values from all output.

Usage:
  python docs/context-cache/phase_0a_key_info_probe.py --env .env
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any


def load_env(path: str | None) -> None:
    if not path:
        return
    with open(path, "r", encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw[:500]}
        return exc.code, payload


def redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key.lower() in {"key", "token", "api_key"} and isinstance(item, str):
                redacted[key] = redact_key(item)
            else:
                redacted[key] = redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def redact_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return "<redacted>"
    return f"{key[:5]}...{key[-4:]}"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def generate_key(base_url: str, master_key: str, alias: str, **fields: Any) -> str:
    body = {
        "key_alias": alias,
        "metadata": {"phase": "0A", "synthetic": True},
        **fields,
    }
    status, payload = request("POST", f"{base_url}/key/generate", master_key, body)
    if status >= 300:
        raise RuntimeError(f"key/generate failed for {alias}: HTTP {status} {payload}")
    key = payload.get("key")
    if not isinstance(key, str):
        raise RuntimeError(f"key/generate did not return a key for {alias}")
    return key


def cleanup_key(base_url: str, master_key: str, key: str) -> None:
    request("POST", f"{base_url}/key/delete", master_key, {"keys": [key]})


def record(results: list[dict[str, Any]], name: str, status: int, payload: dict[str, Any]) -> None:
    info = payload.get("info") if isinstance(payload, dict) else None
    entry = {
        "case": name,
        "http_status": status,
        "key_status": info.get("status") if isinstance(info, dict) else None,
        "returned_fields": sorted(info.keys()) if isinstance(info, dict) else [],
        "payload": redact_payload(payload),
    }
    results.append(entry)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", help="Optional .env file containing LITELLM_MASTER_KEY")
    parser.add_argument("--base-url", default=os.getenv("LITELLM_BASE_URL", "http://localhost:4000"))
    args = parser.parse_args()

    load_env(args.env)
    base_url = args.base_url.rstrip("/")
    master_key = os.getenv("LITELLM_MASTER_KEY")
    if not master_key:
        print("LITELLM_MASTER_KEY is required", file=sys.stderr)
        return 2

    stamp = int(time.time())
    created: list[str] = []
    results: list[dict[str, Any]] = []

    try:
        valid_key = generate_key(
            base_url,
            master_key,
            f"phase0a-valid-{stamp}",
            models=["smart-router"],
            user_id=f"phase0a-user-{stamp}",
        )
        created.append(valid_key)
        status, payload = request("GET", f"{base_url}/key/info", valid_key)
        record(results, "valid key self /key/info", status, payload)

        invalid_status, invalid_payload = request("GET", f"{base_url}/key/info", "sk-phase0a-invalid-key")
        record(results, "invalid key self /key/info", invalid_status, invalid_payload)

        revoked_key = generate_key(base_url, master_key, f"phase0a-revoked-{stamp}", models=["smart-router"])
        created.append(revoked_key)
        request("POST", f"{base_url}/key/block", master_key, {"key": revoked_key})
        status, payload = request("GET", f"{base_url}/key/info", revoked_key)
        record(results, "revoked key self /key/info", status, payload)
        status, payload = request("GET", f"{base_url}/key/info?key={sha256(revoked_key)}", master_key)
        record(results, "revoked key service /key/info by hash", status, payload)

        expired_key = generate_key(
            base_url,
            master_key,
            f"phase0a-expired-{stamp}",
            models=["smart-router"],
            duration="1s",
        )
        created.append(expired_key)
        time.sleep(2)
        status, payload = request("GET", f"{base_url}/key/info", expired_key)
        record(results, "expired key self /key/info", status, payload)
        status, payload = request("GET", f"{base_url}/key/info?key={sha256(expired_key)}", master_key)
        record(results, "expired key service /key/info by hash", status, payload)

        other_model_key = generate_key(base_url, master_key, f"phase0a-other-model-{stamp}", models=["openai-gpt"])
        created.append(other_model_key)
        status, payload = request("GET", f"{base_url}/key/info", other_model_key)
        record(results, "different-model key self /key/info", status, payload)
        status, payload = request(
            "POST",
            f"{base_url}/v1/chat/completions",
            other_model_key,
            {"model": "smart-router", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
        )
        record(results, "different-model key chat authorization", status, payload)

        route_limited_key = generate_key(
            base_url,
            master_key,
            f"phase0a-route-limited-{stamp}",
            models=["smart-router"],
            allowed_routes=["/key/info"],
        )
        created.append(route_limited_key)
        status, payload = request("GET", f"{base_url}/key/info", route_limited_key)
        record(results, "route-limited key self /key/info", status, payload)
        status, payload = request(
            "POST",
            f"{base_url}/v1/chat/completions",
            route_limited_key,
            {"model": "smart-router", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
        )
        record(results, "route-limited key chat authorization", status, payload)

    finally:
        for key in created:
            cleanup_key(base_url, master_key, key)

    print(json.dumps({"base_url": base_url, "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
