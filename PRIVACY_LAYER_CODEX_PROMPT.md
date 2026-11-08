# Codex Implementation Prompt: Add a Reversible Privacy Layer to LiteLLM

## Mission

Implement a configurable, reversible data-privacy layer in the existing LiteLLM Proxy project. Detect sensitive information in incoming user messages using built-in PII recognizers and user-configurable templates, replace detected values with request-scoped tokens before any external LLM sees them, and restore approved values in the model response before returning it to the client.

**Hard constraint: the existing prompt classifier and routing service must remain untouched.** Do not edit, rename, refactor, reformat, or change behavior in any classifier or routing-service files. Do not change their APIs or configuration. Add the privacy implementation as a separate module/package and integrate it only through the smallest compatible LiteLLM proxy hook/configuration changes. If the required hook cannot safely support the current LiteLLM version or request path, stop and explain the constraint before attempting an architectural rewrite.

The project currently uses LiteLLM Proxy 1.103.2, Windows/PowerShell, Docker Compose, a local Ollama provider, and an OpenAI provider. Preserve the existing client contract and model-routing behavior.

## Required workflow — inspect before editing

1. Inspect the repository, current LiteLLM version, Docker Compose files, proxy config, Python environment, and how the proxy is launched.
2. Identify the exact existing classifier and routing-service paths. Record them as protected files.
3. Read the LiteLLM 1.103.2 hook/plugin interfaces and verify request/response hook behavior against the installed version or its pinned documentation/source. Do not assume a hook signature from a different version.
4. Write a short implementation plan before making changes.
5. Make changes only in new privacy-specific files and the minimum necessary proxy integration/configuration files. Do not touch classifier or routing-service files.
6. Run tests and report actual results. Do not claim a test passed unless it was executed.

## Functional requirements

### 1. Detection engine

Use Microsoft Presidio where appropriate:

- Install `presidio-analyzer` and `presidio-anonymizer` in the correct dependency file for this repository.
- Install a compatible spaCy English model only if needed by the selected Presidio recognizers, and document the exact installation command. Do not download models at application startup.
- Use Presidio's AnalyzerEngine and custom recognizers for common PII such as email addresses, phone numbers, person names, locations, dates, and relevant built-in entities.
- Add configurable regex/template recognizers for organization-specific identifiers such as customer IDs, employee IDs, account IDs, internal ticket IDs, and other patterns supplied by the user.
- Include a sample `privacy/policies.yaml` (or a similarly named configuration file) with entities enabled/disabled, confidence thresholds, masking strategies, and example patterns.
- Support configuration reload on restart. Do not introduce a new database just to store templates.
- Handle overlapping detections deterministically (prefer the best supported span/confidence and avoid corrupting text).
- Document that detection is probabilistic and cannot guarantee detection of every sensitive value.
- Keep detection local. Do not send raw prompts to an external model for PII detection.

Initial scope: PII and configurable structured identifiers. Also support policy rules for secrets (API keys, access tokens, passwords, private keys) using high-confidence patterns. For secrets, default to **redact**, not reversible tokenization, unless an explicit policy safely requires restoration. Never restore credentials into an LLM-generated response by default.

### 2. Reversible tokenization

Create a dedicated masking component that:

- Replaces each detected sensitive span with a unique, unpredictable, request-scoped token, for example `⟦PII_EMAIL_<random-id>⟧`.
- Uses a cryptographically secure random generator; do not derive tokens from the sensitive value.
- Stores the mapping from token to original value only in a private per-request context or protected short-lived vault.
- Never sends the token mapping, original values, or vault identifiers containing sensitive data to the classifier, routing service, model provider, logs, traces, metrics, or client.
- Handles repeated occurrences consistently within a single request where safe, while preserving entity distinctions (e.g. two different email addresses must map to different tokens).
- Ensures tokens do not collide with text already present in the original prompt. If a collision is found, generate new tokens.
- Uses safe data structures and strict request isolation. Do not use a shared global mutable dictionary for mappings.
- Does not persist mappings by default. If the existing proxy lifecycle requires cross-worker storage, explain why and use a protected TTL-based store with access controls and encryption where appropriate. Do not add Redis or another service unless genuinely required.
- Clears request mappings on success, failure, timeout, cancellation, and client disconnect as far as the chosen integration permits.

### 3. Message handling

- Inspect all relevant user-provided text fields in supported chat messages, not just the final user message. Include system/developer messages only if they can contain user-supplied content; do not alter trusted proxy-owned system configuration without an explicit need.
- Preserve message roles, ordering, non-text content, tool-call structure, and unrelated request fields.
- If multimodal/image/audio payloads are not inspected, document this clearly and do not imply they are protected.
- Do not mutate shared request objects in place if that could affect concurrent requests or later hooks.
- Apply masking **before both the prompt classifier and the destination LLM/provider**. The existing classifier/routing service must receive only the masked prompt, so sensitive values cannot leak through the classification path.
- Keep the existing classifier and routing service unchanged. If the current integration order means the classifier sees the raw prompt before the proposed hook, do not silently accept that gap. Find a supported integration point that sanitizes before classification, or stop and explain the required architectural trade-off.

### 4. Response restoration

- Restore only exact tokens present in the current request's private mapping.
- Validate every candidate token against that mapping; never use a token to look up arbitrary external data.
- Do not restore redacted secrets.
- Do not replace token-like strings that are not registered for the current request.
- Handle repeated tokens and tokens in different response segments.
- If a token is malformed, unknown, missing, or corrupted, do not guess. Keep the safe placeholder and log only a non-sensitive event/count.
- Do not restore sensitive values into model-generated text unless the token is valid for the current request and the entity policy explicitly permits restoration.
- Consider prompt injection: model output must not be allowed to manufacture a valid token or retrieve mappings for another request.
- If a provider returns tool calls or structured output, preserve the structure and apply restoration only to text fields that are actually returned to the client and are covered by the implementation.
- If a response format cannot safely be restored, fail safely and document the behavior.

### 5. Streaming and non-streaming

- Support non-streaming chat completions first, with integration tests.
- Investigate streaming support for the exact installed LiteLLM version. A token may be split across streamed chunks; naive per-chunk replacement is not acceptable.
- If streaming restoration can be implemented safely through supported hooks, buffer enough output to detect and restore tokens while preserving the stream protocol as closely as possible.
- If safe streaming is not feasible through supported hooks, explicitly disable privacy-protected streaming or reject streaming requests when the privacy layer is enabled, with a clear error. Do not silently stream masked tokens or leak original data.
- Document any unsupported endpoint types (embeddings, responses API, image/audio, files, batch, tool calls) and ensure they cannot bypass the privacy policy unnoticed. Either implement safe coverage or explicitly reject/mark them unsupported.

### 6. Fail-safe behavior and security

- Default to fail-closed for requests that require privacy protection: if detection or masking fails, do not forward the original prompt to a provider.
- Add configurable behavior for detection errors, with a secure default.
- Do not log raw prompts, detected values, mappings, secrets, or unmasked model responses.
- Log only safe metadata such as request correlation ID, entity types/counts, policy version, outcome, and latency. Ensure exception messages do not include raw content.
- Check existing LiteLLM prompt logging/storage configuration. Do not silently change unrelated logging behavior, but document any settings required to prevent raw input/output retention. Privacy must cover application logs, traces, callbacks, and prompt storage, not only the provider request.
- Use environment variables for secrets and configuration overrides; never commit credentials.
- Avoid logging tokens if tokens could be correlated with sensitive data; if logged at all, log only counts/types, not token strings.
- Set explicit request/hook timeouts and clean up context reliably.
- Avoid claiming regulatory compliance or perfect PII detection.

## Architecture and file boundaries

Prefer a separate package similar to:

```text
privacy/
  __init__.py
  config.py
  detector.py
  template_loader.py
  masker.py
  token_vault.py
  response_restorer.py
  policy.py
  logging_utils.py
  policies.yaml
plugins/
  privacy_hook.py              # thin LiteLLM adapter, if compatible with existing layout
tests/
  privacy/
    test_detection.py
    test_custom_templates.py
    test_masking.py
    test_restoration.py
    test_request_isolation.py
    test_fail_closed.py
    test_streaming_or_streaming_rejection.py
```

Adapt this to the repository's actual layout. Do not duplicate existing code unnecessarily.

**Protected files:** every existing classifier file and every existing routing-service file. Do not edit them under any circumstances. Before finishing, use `git diff --name-only` and verify none of the protected files changed. If the repo is not under Git, compare file hashes or inspect changes manually.

## LiteLLM integration

- Verify the actual LiteLLM 1.103.2 hook/plugin interface before implementation.
- Keep the privacy engine independent of LiteLLM imports where practical.
- The LiteLLM adapter should call the privacy package and do minimal request/response transformation.
- Integrate through supported hooks/configuration and retain the existing endpoint, router, aliases, and routing behavior.
- Do not edit the classifier plugin, routing-service `app.py`, classifier rules, embedding logic, classifier prompts, routing tiers, or model aliases.
- If a proxy hook cannot guarantee masking before the current classifier, do not implement an incomplete “privacy” feature. Explain exactly why and propose the smallest integration point that preserves the no-touch constraint for classifier/routing files.
- Ensure request-scoped mappings are available for the corresponding response only, including concurrent requests. Do not key mappings solely by a client-supplied request ID.
- Do not rely on undocumented global variables or fragile monkey-patching.

## Dependencies and installation documentation

Add dependencies to the repository's existing dependency-management approach (requirements file, pyproject, or the relevant Docker image) rather than creating conflicting dependency files.

Document Windows PowerShell commands for local setup, for example:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install presidio-analyzer presidio-anonymizer
```

If a spaCy model is required, include a compatible model installation command such as:

```powershell
python -m spacy download en_core_web_lg
```

Do not blindly use the example model if a smaller compatible model meets the recognizer needs. Verify compatibility with the repository's Python version and dependency constraints. Do not install Python dependencies on the host if the proxy runs only inside Docker; update the correct Docker build/dependency path and document rebuild commands.

If custom recognizers need country-specific phone formats or local language support, explain the additional models/configuration required. Keep model downloads out of runtime startup.

## Testing requirements

Write automated tests using pytest or the repository's existing test framework. Tests must include:

1. Email and phone detection and masking.
2. Custom customer/employee ID templates.
3. Two different values of the same entity type in one request.
4. Repeated occurrence of the same value.
5. Unicode, punctuation, newlines, and adjacent detected spans.
6. Original prompt contains token-like strings; generated tokens must avoid collisions.
7. Only registered current-request tokens are restored.
8. Unknown/forged tokens are not restored.
9. Secret/API-key policy redacts and never restores credentials.
10. Two concurrent requests cannot access each other's mappings.
11. Detector/masker failure does not forward an unmasked prompt.
12. Logging and exception handling do not leak sensitive values.
13. Non-streaming LiteLLM integration using a mocked provider.
14. Streaming is correctly restored or safely rejected according to the implementation.
15. Existing classifier/routing behavior remains unchanged.

Use synthetic test data only. Never use real personal data or credentials in tests.

Run the unit tests, lint/type checks available in the project, and a smoke test of the proxy where feasible. Clearly distinguish tests executed from tests not executed because Docker/Ollama/OpenAI credentials or services were unavailable. Never make live paid OpenAI calls as part of automated tests.

## Documentation deliverables

Create or update a `PRIVACY_LAYER.md` document containing:

- Architecture and request/response sequence.
- Exact dependencies and installation steps for Windows and Docker.
- How to add/edit regex templates and entity policies.
- Masking and restoration examples.
- Environment variables and defaults.
- How to enable/disable the privacy layer safely.
- Supported endpoints and streaming behavior.
- Logging, retention, mapping lifetime, and threat-model limitations.
- How to run tests and a local smoke test.
- Known limitations, especially missed detections, multilingual support, and multimodal payloads.

Add a `.env.example` only if useful; never place real keys in it.

## Acceptance criteria

The implementation is complete only when:

- Sensitive values are masked before the classifier/routing service and before any external LLM provider sees the prompt.
- Original values and mappings never reach the classifier, routing service, providers, logs, traces, or persistent prompt storage.
- Only valid tokens from the same request can be restored.
- Secrets are redacted by default and are not restored.
- Concurrent requests are isolated.
- Streaming is safe or explicitly rejected when privacy protection is enabled.
- Detection/masking errors fail closed.
- The existing classifier and routing-service files are unchanged.
- Existing endpoint and routing behavior are preserved.
- Automated tests and setup documentation are added.
- A final report lists files changed, dependencies added, tests actually run, results, and remaining limitations.

## Final instructions to Codex

Proceed incrementally. First inspect and report the plan. Then implement the smallest safe change. Do not refactor unrelated code. Do not modify classifier or routing-service files. If a requirement conflicts with LiteLLM 1.103.2's supported hook lifecycle, stop and explain the conflict rather than weakening the privacy guarantee.
