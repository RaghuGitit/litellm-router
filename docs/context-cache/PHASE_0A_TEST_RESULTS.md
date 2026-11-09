# Phase 0A Test Results

Date: 2026-11-09

## Test Artifacts Created

- `docs/context-cache/phase_0a_key_info_probe.py`
- `docs/context-cache/phase_0a_privacy_reuse_probe.py`

Both are discovery probes, not gateway/cache implementation.

## Environment

Existing compose stack was already running:

- `litellm-router-litellm-1` on port 4000.
- `litellm-router-db-1`.
- `litellm-router-routing-service-1` on port 8000.

The auth probe read `.env` locally for `LITELLM_MASTER_KEY` and redacted all key material from output. It created synthetic keys and attempted cleanup with `/key/delete`.

## Commands Executed

```powershell
python docs\context-cache\phase_0a_privacy_reuse_probe.py
python docs\context-cache\phase_0a_key_info_probe.py --env .env
```

## Privacy Probe Results

Passed:

- Basic lifecycle created a request vault, masked email, restored response, and cleaned up.
- Concurrent requests restored their own emails and did not cross-restore.
- Exception cleanup removed the request vault.
- Cancellation cleanup removed the request vault.

Important limitation confirmed:

- Cached sanitized response from Alice's request did not restore to Bob with Bob's vault.
- The response still contained Alice's first-request PII token.
- Result: per-request random tokens make cached sanitized responses unsafe across request vaults when restorable PII placeholders are present.

Local note:

- Local Python lacked Presidio, so `PrivacyDetector` logged fallback warnings and used regex/custom-pattern recognizers.
- This is compatible with the implementation, but a gateway container should install `privacy-requirements.txt`.

## Authentication Probe Results

Passed:

- Valid key self-validation: `GET /key/info` returned HTTP 200 and `status: active`.
- Invalid key self-validation: returned HTTP 401.
- Revoked/blocked key self-validation: returned HTTP 401.
- Revoked/blocked key service lookup by sha256 hash: returned HTTP 200 with `status: revoked`.
- Expired key self-validation: returned HTTP 401.
- Expired key service lookup by sha256 hash: returned HTTP 200 with `status: expired`.
- Key restricted to `openai-gpt`: `GET /key/info` returned active metadata, but chat request to `smart-router` returned HTTP 403 `key_model_access_denied`.
- Key restricted to `allowed_routes: ["/key/info"]`: self `GET /key/info` returned active metadata, but chat request returned HTTP 403 for route restriction.

Observed valid-key identity/authorization fields:

- Top-level key hash.
- `user_id`.
- `models`.
- `allowed_routes`.
- `status`.
- `blocked`.
- `expires`.
- `metadata`.
- budget/rate-limit fields.
- org/project/team fields when present on row.

## Interpretation

`GET /key/info` self-validation is a supported and practical way for the gateway to validate the client's existing virtual key without duplicating LiteLLM auth.

For service/admin lookup, `GET /key/info?key=<sha256>` can inspect arbitrary keys, including revoked and expired keys, but it returns status rather than rejecting those rows. Gateway code using service lookup must explicitly require `status == "active"`.

`/key/info` proves identity and reports restrictions, but it does not by itself prove that a specific chat request would pass all LiteLLM model/route/budget/object-permission checks. Cache hits must therefore be conservative and bypass cache when authorization cannot be proven from returned fields.

## Synthetic Secrets

No raw virtual keys or master keys are recorded in this document. Probe output redacted keys and used hashes or abbreviated values only.
