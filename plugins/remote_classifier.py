"""LiteLLM 1.103.2 custom classifier adapter.

LiteLLM calls `async classify(context)` and expects a tier name or None.
The classification algorithm itself lives in the routing-service container.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)
VALID_TIERS = {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}


class RemoteClassifier:
    async def classify(self, context) -> str | None:
        url = os.getenv(
            "ROUTER_SERVICE_URL",
            "http://routing-service:8000/route",
        )
        messages = getattr(context, "structured_messages", None)
        if messages is None:
            messages = getattr(context, "raw_messages", [])
        try:
            async with httpx.AsyncClient(timeout=9.5) as client:
                response = await client.post(
                    url,
                    json={
                        "messages": messages or [],
                        "metadata": {},
                    },
                )
                response.raise_for_status()
                payload = response.json()
            tier = payload.get("tier")
            if tier not in VALID_TIERS:
                logger.warning("Routing service returned invalid tier")
                return None
            logger.info(
                "Remote classifier result tier=%s method=%s policy=%s",
                tier, payload.get("method"), payload.get("policy_version")
            )
            return tier
        except Exception:
            logger.exception("Remote classifier failed; delegating to LiteLLM fallback")
            return None


remote_classifier = RemoteClassifier()
