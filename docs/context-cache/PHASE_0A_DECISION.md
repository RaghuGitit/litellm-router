# Phase 0A Decision

Date: 2026-11-09

## Decision Summary

Gateway authentication is feasible without duplicating LiteLLM virtual-key validation.

Use `GET /key/info` self-validation with the client's existing virtual key as the primary mechanism. This is supported in the installed LiteLLM 1.103.2 source and documented for virtual-key info lookup. `POST /key/info` was not found in this version; `POST /v2/key/info` exists but is hidden from schema and documented in-source as currently admin only.

Existing `PrivacyService` can be safely reused directly by a gateway for request masking, response restoration within the same request vault, and cleanup.

Cached-response restoration across different request vaults is not safe when the cached sanitized response contains restorable PII placeholders, because tokens are random and request-scoped.

## Feasible First Implementation

Build a minimal gateway for non-streaming `POST /v1/chat/completions` with:

- `GET /key/info` self-validation.
- Trusted identity derived only from LiteLLM-returned key info.
- Scope hierarchy: organization, project, team, user, key.
- Existing `PrivacyService` imported directly.
- Cache lookup after privacy masking.
- Cache miss forwarding to LiteLLM with the original virtual key.
- Cache admission only for sanitized responses that contain no restorable PII tokens.
- Conservative authorization on cache hits; bypass cache when model/route authorization cannot be proven from returned metadata.

## Blockers / Design Constraints

Full cache-hit parity with LiteLLM authorization is not proven from `/key/info` alone.

The gateway must not serve cache hits when any of these are ambiguous:

- inherited team/user/project/org model access,
- access groups,
- wildcard models,
- fallback model authorization,
- object permissions,
- budget/rate-limit side effects,
- route groups the gateway does not explicitly understand.

The privacy token model blocks general cross-request cached restoration for responses containing restorable PII tokens.

## Recommended Next Phase

Proceed to a narrow Phase 1 gateway skeleton only if these guardrails are accepted:

1. Non-streaming chat completions only.
2. Auth via `GET /key/info` self-validation.
3. Cache-hit only when `status == "active"`, route/model are conservatively proven, and cached response has no restorable PII tokens.
4. All uncertain cases bypass cache and forward to LiteLLM.
5. Original virtual key is forwarded on misses.
6. No changes to classifier, routing service, `config.yaml` routing behavior, `plugins/privacy_hook.py`, or `privacy/**`.

If broader cache reuse over PII-bearing responses is required, a later design phase must introduce a safe response templating or deterministic placeholder strategy. That would be an architectural change and should not be folded into the first gateway implementation.

## Files Inspected

Repository files:

- `config.yaml`
- `docker-compose.quickstart.yml`
- `Dockerfile.litellm`
- `privacy-requirements.txt`
- `plugins/privacy_hook.py`
- `privacy/__init__.py`
- `privacy/service.py`
- `privacy/detector.py`
- `privacy/masker.py`
- `privacy/response_restorer.py`
- `privacy/token_vault.py`
- `privacy/policy.py`
- `privacy/policies.yaml`

Installed LiteLLM files:

- `litellm/proxy/management_endpoints/key_management_endpoints.py`
- `litellm/proxy/auth/user_api_key_auth.py`
- `litellm/proxy/auth/route_checks.py`
- `litellm/proxy/_types.py`
- `litellm/proxy/litellm_pre_call_utils.py`

New Phase 0A artifacts:

- `docs/context-cache/phase_0a_key_info_probe.py`
- `docs/context-cache/phase_0a_privacy_reuse_probe.py`
- `docs/context-cache/PHASE_0A_AUTHENTICATION_ANALYSIS.md`
- `docs/context-cache/PHASE_0A_PRIVACY_REUSE_ANALYSIS.md`
- `docs/context-cache/PHASE_0A_GATEWAY_ARCHITECTURE.md`
- `docs/context-cache/PHASE_0A_TEST_RESULTS.md`
- `docs/context-cache/PHASE_0A_DECISION.md`

Protected files were not modified.
