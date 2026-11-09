# Phase 0A Authentication Analysis

Date: 2026-11-09

## Scope

This document evaluates whether a separate Context Cache Gateway can validate existing LiteLLM virtual keys without duplicating LiteLLM authentication or creating another virtual-key database.

## Sources Inspected

Installed LiteLLM 1.103.2 image:

- `/app/.venv/lib/python3.13/site-packages/litellm/proxy/management_endpoints/key_management_endpoints.py`
- `/app/.venv/lib/python3.13/site-packages/litellm/proxy/auth/user_api_key_auth.py`
- `/app/.venv/lib/python3.13/site-packages/litellm/proxy/auth/route_checks.py`
- `/app/.venv/lib/python3.13/site-packages/litellm/proxy/_types.py`
- `/app/.venv/lib/python3.13/site-packages/litellm/proxy/litellm_pre_call_utils.py`

Official docs:

- `https://docs.litellm.ai/docs/proxy/virtual_keys`
- `https://docs.litellm.ai/api-reference`

## Endpoint Availability

In LiteLLM 1.103.2:

- `GET /key/info` exists, is in the OpenAPI/schema-facing key management routes, and is protected by `Depends(user_api_key_auth)`.
- `POST /v2/key/info` exists, is hidden from schema in this version, accepts a list of keys/key aliases, and its docstring says it is currently admin only.
- `POST /key/info` was not found in the installed 1.103.2 source.

The official virtual-key docs document `GET /key/info?key=<user-key>` with `Authorization: Bearer <your-master-key>` and say it returns spend/key metadata. The installed source is more security-hardened than the doc example: it prefers passing the sha256 key hash in the query because raw query parameters are logged by access logs.

## Authentication Requirements

`GET /key/info` always runs the normal `user_api_key_auth` dependency first.

Supported validation modes:

- Self-validation: `GET /key/info` with `Authorization: Bearer <candidate virtual key>` and no `key` query parameter.
- Service lookup: `GET /key/info?key=<sha256(candidate key)>` with `Authorization: Bearer <master/admin/service key>`.

Self-validation is the better gateway default because LiteLLM itself rejects invalid, blocked, and expired keys before the handler returns. It does not require giving the gateway a LiteLLM master key. Service lookup is useful for admin tools, but the gateway must treat returned `status != "active"` as deny.

## Behavior Verified From Source

`GET /key/info`:

- Defaults to the auth token when no `key` query parameter is supplied.
- Hashes plaintext keys before lookup.
- Looks up live key rows and then deleted-key archive rows.
- Returns `status` as `active`, `expired`, `revoked`, or `deleted`.
- Removes the stored hashed `token` field from `info` before returning.
- Allows access when the caller is proxy admin/viewer, the caller is querying its own key, the caller owns the key's `user_id`, or the caller belongs to the key's team.

Normal auth rejects:

- Invalid keys: no token found.
- Blocked/revoked keys: `valid_token.blocked is True`.
- Expired keys: expiry before current UTC time.
- Model-restricted keys when used against disallowed models.
- Route-restricted keys when used against disallowed routes.

## Returned Identity And Authorization Fields

Observed and source-confirmed useful fields include:

- Top-level `key`: hash/identifier for the looked-up key.
- `info.user_id`
- `info.team_id`
- `info.organization_id`
- `info.project_id`
- `info.models`
- `info.allowed_routes`
- `info.permissions`
- `info.object_permission`
- `info.access_group_ids`
- `info.status`
- `info.blocked`
- `info.expires`
- budgets/rate limits: `max_budget`, `budget_duration`, `budget_limits`, `model_max_budget`, `rpm_limit`, `tpm_limit`, `tpd_limit`
- key metadata: `key_alias`, `key_name`, `metadata`

These fields are sufficient to build a conservative cache isolation identity. They are not sufficient by themselves to fully replay every LiteLLM authorization path on cache hits, especially inherited model access from users, teams, projects, organizations, access groups, wildcard model grants, fallback models, object permissions, and budget side effects.

## Request/Response Examples

Self-validation request:

```http
GET /key/info HTTP/1.1
Host: localhost:4000
Authorization: Bearer sk-<candidate-virtual-key>
```

Successful synthetic response shape with secrets removed:

```json
{
  "key": "<sha256-key-hash>",
  "info": {
    "status": "active",
    "user_id": "phase0a-user-...",
    "team_id": null,
    "organization_id": null,
    "project_id": null,
    "models": ["smart-router"],
    "allowed_routes": [],
    "blocked": null,
    "expires": null,
    "metadata": {
      "phase": "0A",
      "synthetic": true
    }
  }
}
```

Service lookup request:

```http
GET /key/info?key=<sha256-candidate-key> HTTP/1.1
Host: localhost:4000
Authorization: Bearer sk-<admin-or-service-key>
```

Revoked synthetic response shape:

```json
{
  "key": "<sha256-key-hash>",
  "info": {
    "status": "revoked",
    "blocked": true,
    "models": ["smart-router"]
  }
}
```

## Gateway Recommendation

Use `GET /key/info` self-validation as the first implementation mechanism:

1. Gateway receives the existing LiteLLM virtual key from the client.
2. Gateway calls LiteLLM `GET /key/info` using that key as the bearer token.
3. Gateway accepts only HTTP 200 with `info.status == "active"`.
4. Gateway constructs a trusted identity from returned fields only.
5. Gateway forwards the original client key to LiteLLM on cache misses.

For cache hits, preserve authorization conservatively:

- If `allowed_routes` is non-empty, only serve from cache when the requested route is explicitly allowed by a gateway-supported exact/prefix rule; otherwise bypass cache and forward to LiteLLM.
- If `models` is non-empty, only serve from cache when the requested model exactly matches a returned allowed model or a deliberately supported LiteLLM special model pattern; otherwise bypass cache.
- If the key relies on inherited team/user/project/org permissions, access groups, wildcard grants, or ambiguous defaults, bypass cache until a supported effective-authorization endpoint is verified.

This preserves LiteLLM behavior by sacrificing hit rate when authorization cannot be proven.
