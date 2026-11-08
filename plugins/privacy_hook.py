from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger

from privacy.service import PrivacyService

log = logging.getLogger("privacy.hook")


class PrivacyHook(CustomLogger):
    enforces_request_content = True

    def __init__(self) -> None:
        super().__init__()
        self.enabled = os.getenv("PRIVACY_LAYER_ENABLED", "true").lower() not in {"0", "false", "no"}
        self.service = PrivacyService()

    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type: str):
        if not self.enabled or not self.service.policy.enabled:
            return data
        request_key = id(data)
        try:
            if call_type not in {"acompletion", "completion"}:
                raise HTTPException(status_code=400, detail="Privacy layer supports chat completions only")
            if data.get("stream") is True and self.service.policy.reject_streaming:
                raise HTTPException(status_code=400, detail="Streaming is disabled while privacy protection is enabled")
            messages = data.get("messages")
            if not isinstance(messages, list):
                raise HTTPException(status_code=400, detail="Privacy layer requires chat messages")

            request_vault = self.service.start_request(request_key)
            masked = self.service.mask_request_messages(messages, request_vault)
            data["messages"] = masked.messages
            log.info(
                "privacy_masked policy=%s tokenized=%d redacted=%d entity_types=%s",
                self.service.policy.version,
                masked.stats.tokenized_count,
                masked.stats.redacted_count,
                sorted(masked.stats.entity_counts),
            )
            return data
        except HTTPException:
            self.service.cleanup(request_key)
            raise
        except Exception as exc:
            self.service.cleanup(request_key)
            log.warning("privacy_mask_failed error_type=%s", type(exc).__name__)
            if self.service.policy.fail_closed:
                raise HTTPException(status_code=500, detail="Privacy masking failed; request was not forwarded")
            return data

    async def async_post_call_success_hook(self, data: dict, user_api_key_dict, response: Any):
        if not self.enabled or not self.service.policy.enabled:
            return response
        request_key = id(data)
        try:
            return self.service.restore_response(response, request_key)
        finally:
            self.service.cleanup(request_key)

    async def async_post_call_failure_hook(
        self,
        request_data: dict,
        original_exception: Exception,
        user_api_key_dict,
        traceback_str: str | None = None,
    ):
        self.service.cleanup(id(request_data))
        return None


privacy_hook = PrivacyHook()
