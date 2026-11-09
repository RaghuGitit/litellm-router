# Phase 0A Privacy Reuse Analysis

Date: 2026-11-09

## Files Inspected

- `privacy/__init__.py`
- `privacy/service.py`
- `privacy/detector.py`
- `privacy/masker.py`
- `privacy/response_restorer.py`
- `privacy/token_vault.py`
- `privacy/policy.py`
- `privacy/policies.yaml`
- `plugins/privacy_hook.py`

## Public API

`PrivacyService` constructor:

```python
PrivacyService(policy_path: str | Path | None = None) -> None
```

Public methods:

```python
start_request(request_key: int) -> RequestVault
mask_request_messages(messages: list[dict[str, Any]], request_vault: RequestVault) -> MaskingResult
restore_response(response: Any, request_key: int) -> Any
cleanup(request_key: int) -> None
```

Supporting public behavior:

- `RequestVault.register(token, original, entity_type, restore)`
- `RequestVault.token_for_value(entity_type, original)`
- `RequestVault.lookup(token)`
- `ResponseRestorer.restore_text(text, vault)`
- `ResponseRestorer.restore_response(response, vault)`

## Initialization Requirements

`PrivacyService` loads policy from:

- Explicit `policy_path`, or
- `PRIVACY_POLICY_PATH`, or
- `privacy/policies.yaml`.

It initializes:

- `PrivacyPolicy`
- `PrivacyDetector`
- `PrivacyMasker`
- `ResponseRestorer`
- `TokenVault`

The detector attempts to initialize Presidio with a `NoOpNlpEngine`. If Presidio is unavailable, it logs a warning and falls back to local regex/custom-pattern detection.

Runtime dependencies:

- `PyYAML`
- `presidio-analyzer`
- `presidio-anonymizer`

The local probe ran without Presidio installed and used fallback pattern recognizers. The LiteLLM container installs `privacy-requirements.txt`, so a gateway container should install the same requirements.

## Request Vault Lifecycle

The privacy engine itself does not require `id(data)` or LiteLLM-specific objects. That is only how `plugins/privacy_hook.py` chooses its `request_key`.

A gateway can safely choose an explicit per-request integer or UUID-derived key:

1. `vault = service.start_request(request_key)`
2. `masked = service.mask_request_messages(messages, vault)`
3. Forward sanitized messages or use them for cache keying.
4. `service.restore_response(response, request_key)`
5. `service.cleanup(request_key)` in `finally`.

## Concurrency

The tested concurrent async case used one `PrivacyService` instance and separate request keys. Requests stayed isolated.

Important boundary:

- `TokenVault` stores request vaults in an in-memory dict.
- It is suitable for ordinary single-process FastAPI async usage with unique request keys and mandatory cleanup.
- It should not be used as a cross-process/shared cache state.
- If a gateway uses multiple worker processes, each request must complete within the worker that created its vault.

## Global Mutable State

No module-level singleton is required by `PrivacyService`. `plugins/privacy_hook.py` creates a module-level `privacy_hook = PrivacyHook()`, but a gateway can create its own `PrivacyService` instance directly.

Cross-request leakage risk is controlled by:

- Unique request keys.
- Cleanup in success, exception, and cancellation paths.
- Never storing `RequestVault` in the context cache.

## LiteLLM Hook Dependency

Masking, restoration, and cleanup can be performed independently of LiteLLM hooks.

The hook adds policy enforcement around:

- Supported call types.
- Streaming rejection.
- Fail-closed behavior on masking failure.
- Cleanup in post-success and post-failure hooks.

A gateway must reproduce those lifecycle decisions without modifying `plugins/privacy_hook.py`.

## Cache-Reuse Limitation

The current tokenizer creates random per-request PII tokens. A sanitized response cached from request A contains request A's tokens. Request B's vault does not know those tokens, even if request B has equivalent content with different PII.

Observed result:

- Request A: `alice@example.com` became token A.
- Cached sanitized response contained token A.
- Request B: `bob@example.com` became token B.
- Restoring cached response with request B's vault did not produce Bob; token A remained unresolved.

Therefore cached sanitized responses that contain restorable PII placeholders are not safe to reuse across request vaults.

First implementation options:

- Admit only cache entries whose stored response contains no restorable PII tokens.
- Or cache only non-sensitive derived artifacts until deterministic/template restoration is designed.
- Do not alter token generation in this phase.

## Conclusion

`PrivacyService` can be reused directly by an independent gateway for request masking, response restoration, and cleanup. It cannot by itself make cached sanitized responses portable across different request vaults when responses contain restorable PII tokens.
