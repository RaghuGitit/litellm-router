from __future__ import annotations

import copy
from typing import Any

from .masker import TOKEN_PATTERN
from .token_vault import RequestVault


class ResponseRestorer:
    def restore_response(self, response: Any, vault: RequestVault) -> Any:
        if isinstance(response, dict):
            response_obj = copy.deepcopy(response)
            self._restore_dict_response(response_obj, vault)
            return response_obj
        self._restore_object_response(response, vault)
        return response

    def restore_text(self, text: str, vault: RequestVault) -> str:
        def replace(match):
            token = match.group(0)
            record = vault.lookup(token)
            if record is None or not record.restore:
                return token
            return record.original

        return TOKEN_PATTERN.sub(replace, text)

    def _restore_dict_response(self, response: dict[str, Any], vault: RequestVault) -> None:
        for choice in response.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or choice.get("delta")
            if isinstance(message, dict):
                self._restore_message(message, vault)
        if isinstance(response.get("output_text"), str):
            response["output_text"] = self.restore_text(response["output_text"], vault)

    def _restore_object_response(self, response: Any, vault: RequestVault) -> None:
        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        for choice in choices or []:
            message = getattr(choice, "message", None) or getattr(choice, "delta", None)
            if message is None and isinstance(choice, dict):
                message = choice.get("message") or choice.get("delta")
            if message is not None:
                self._restore_message(message, vault)

    def _restore_message(self, message: Any, vault: RequestVault) -> None:
        content = _get(message, "content")
        if isinstance(content, str):
            _set(message, "content", self.restore_text(content, vault))
        tool_calls = _get(message, "tool_calls")
        for tool_call in tool_calls or []:
            function = _get(tool_call, "function")
            if function is not None:
                arguments = _get(function, "arguments")
                if isinstance(arguments, str):
                    _set(function, "arguments", self.restore_text(arguments, vault))


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)

