"""Phase 0A privacy reuse probe.

Runs against the repository's existing privacy package without modifying it.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from privacy.service import PrivacyService


def message(text: str) -> list[dict[str, Any]]:
    return [{"role": "user", "content": text}]


def assert_not_contains(text: str, forbidden: list[str]) -> None:
    for value in forbidden:
        if value in text:
            raise AssertionError(f"unexpected sensitive value in sanitized text: {value}")


def basic_lifecycle() -> dict[str, Any]:
    service = PrivacyService()
    request_key = 1001
    vault = service.start_request(request_key)
    masked = service.mask_request_messages(message("Alice Person can be reached at alice@example.com."), vault)
    sanitized = masked.messages[0]["content"]
    assert_not_contains(sanitized, ["alice@example.com"])

    response = {"choices": [{"message": {"content": f"Reply to {sanitized}"}}]}
    restored = service.restore_response(response, request_key)
    restored_text = restored["choices"][0]["message"]["content"]
    if "alice@example.com" not in restored_text:
        raise AssertionError("email was not restored in response")
    service.cleanup(request_key)
    if service.vault.get(request_key) is not None:
        raise AssertionError("request vault was not cleaned up")
    return {
        "sanitized_contains_email": "alice@example.com" in sanitized,
        "restored_contains_email": "alice@example.com" in restored_text,
        "tokenized_count": masked.stats.tokenized_count,
    }


async def concurrent_requests() -> dict[str, Any]:
    service = PrivacyService()

    async def run_one(request_key: int, email: str) -> str:
        vault = service.start_request(request_key)
        masked = service.mask_request_messages(message(f"Contact {email}."), vault)
        restored = service.restore_response(
            {"choices": [{"message": {"content": masked.messages[0]["content"]}}]},
            request_key,
        )
        service.cleanup(request_key)
        return restored["choices"][0]["message"]["content"]

    one, two = await asyncio.gather(
        run_one(2001, "alice@example.com"),
        run_one(2002, "bob@example.com"),
    )
    if "alice@example.com" not in one or "bob@example.com" not in two:
        raise AssertionError("concurrent request restoration failed")
    return {
        "request_one_restored": "alice@example.com" in one,
        "request_two_restored": "bob@example.com" in two,
    }


def cached_response_cross_request() -> dict[str, Any]:
    service = PrivacyService()

    first_key = 3001
    first_vault = service.start_request(first_key)
    first_masked = service.mask_request_messages(message("Email Alice at alice@example.com."), first_vault)
    first_sanitized = first_masked.messages[0]["content"]
    cached_sanitized_response = {
        "choices": [{"message": {"content": f"Cached answer for {first_sanitized}"}}]
    }

    second_key = 3002
    second_vault = service.start_request(second_key)
    second_masked = service.mask_request_messages(message("Email Bob at bob@example.com."), second_vault)
    second_sanitized = second_masked.messages[0]["content"]
    restored_with_second = service.restore_response(cached_sanitized_response, second_key)
    restored_text = restored_with_second["choices"][0]["message"]["content"]

    service.cleanup(first_key)
    service.cleanup(second_key)

    return {
        "first_sanitized": first_sanitized,
        "second_sanitized": second_sanitized,
        "restored_with_second_vault": restored_text,
        "restored_to_bob": "bob@example.com" in restored_text,
        "still_contains_first_request_token": "[[PII_" in restored_text,
        "limitation": (
            "per-request random tokens make cached sanitized responses from one vault "
            "unsafe to restore with another request's vault"
        ),
    }


def failure_cleanup() -> dict[str, Any]:
    service = PrivacyService()
    request_key = 4001
    service.start_request(request_key)
    try:
        raise RuntimeError("synthetic failure")
    except RuntimeError:
        service.cleanup(request_key)
    return {"cleanup_after_exception": service.vault.get(request_key) is None}


async def cancellation_cleanup() -> dict[str, Any]:
    service = PrivacyService()
    request_key = 5001

    async def worker() -> None:
        service.start_request(request_key)
        try:
            await asyncio.sleep(10)
        finally:
            service.cleanup(request_key)

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return {"cleanup_after_cancellation": service.vault.get(request_key) is None}


async def main() -> None:
    result = {
        "basic_lifecycle": basic_lifecycle(),
        "concurrent_requests": await concurrent_requests(),
        "cached_response_cross_request": cached_response_cross_request(),
        "failure_cleanup": failure_cleanup(),
        "cancellation_cleanup": await cancellation_cleanup(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
