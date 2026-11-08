from __future__ import annotations

import copy
import re
import secrets
from dataclasses import dataclass
from typing import Any

from .detector import Detection, PrivacyDetector
from .token_vault import RequestVault

TOKEN_PATTERN = re.compile(r"\[\[PII_[A-Z0-9_]+_[A-Za-z0-9_-]{12,}\]\]")
REDACTED_SECRET = "[[REDACTED_SECRET]]"


@dataclass(frozen=True)
class MaskingStats:
    entity_counts: dict[str, int]
    redacted_count: int
    tokenized_count: int


@dataclass(frozen=True)
class MaskingResult:
    messages: list[dict[str, Any]]
    stats: MaskingStats


class PrivacyMasker:
    def __init__(self, detector: PrivacyDetector):
        self.detector = detector

    def mask_messages(self, messages: list[dict[str, Any]], vault: RequestVault) -> MaskingResult:
        cloned = copy.deepcopy(messages)
        entity_counts: dict[str, int] = {}
        redacted = 0
        tokenized = 0
        original_text = "\n".join(_extract_text_parts(cloned))

        for message in cloned:
            content = message.get("content")
            masked_content, stats_delta = self._mask_content(content, vault, original_text)
            message["content"] = masked_content
            _merge_counts(entity_counts, stats_delta.entity_counts)
            redacted += stats_delta.redacted_count
            tokenized += stats_delta.tokenized_count

            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if isinstance(function, dict) and isinstance(function.get("arguments"), str):
                    masked, stats_delta = self.mask_text(function["arguments"], vault, original_text)
                    function["arguments"] = masked
                    _merge_counts(entity_counts, stats_delta.entity_counts)
                    redacted += stats_delta.redacted_count
                    tokenized += stats_delta.tokenized_count

        return MaskingResult(
            messages=cloned,
            stats=MaskingStats(
                entity_counts=entity_counts,
                redacted_count=redacted,
                tokenized_count=tokenized,
            ),
        )

    def _mask_content(self, content: Any, vault: RequestVault, original_text: str) -> tuple[Any, MaskingStats]:
        if isinstance(content, str):
            return self.mask_text(content, vault, original_text)
        if isinstance(content, list):
            entity_counts: dict[str, int] = {}
            redacted = 0
            tokenized = 0
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    masked, stats_delta = self.mask_text(item["text"], vault, original_text)
                    item["text"] = masked
                    _merge_counts(entity_counts, stats_delta.entity_counts)
                    redacted += stats_delta.redacted_count
                    tokenized += stats_delta.tokenized_count
            return content, MaskingStats(entity_counts, redacted, tokenized)
        return content, MaskingStats({}, 0, 0)

    def mask_text(self, text: str, vault: RequestVault, original_text: str | None = None) -> tuple[str, MaskingStats]:
        detections = self.detector.detect(text)
        if not detections:
            return text, MaskingStats({}, 0, 0)

        pieces: list[str] = []
        cursor = 0
        entity_counts: dict[str, int] = {}
        redacted = 0
        tokenized = 0
        collision_text = original_text if original_text is not None else text

        for detection in detections:
            pieces.append(text[cursor:detection.start])
            original = text[detection.start:detection.end]
            entity_counts[detection.entity_type] = entity_counts.get(detection.entity_type, 0) + 1
            if detection.action == "redact" or not detection.restore:
                pieces.append(REDACTED_SECRET)
                redacted += 1
            else:
                token = vault.token_for_value(detection.entity_type, original)
                if token is None:
                    token = self._new_token(detection, collision_text, vault)
                    vault.register(token, original, detection.entity_type, detection.restore)
                pieces.append(token)
                tokenized += 1
            cursor = detection.end

        pieces.append(text[cursor:])
        return "".join(pieces), MaskingStats(entity_counts, redacted, tokenized)

    @staticmethod
    def _new_token(detection: Detection, original_text: str, vault: RequestVault) -> str:
        safe_entity = re.sub(r"[^A-Z0-9_]", "_", detection.entity_type.upper())
        while True:
            token = f"[[PII_{safe_entity}_{secrets.token_urlsafe(12)}]]"
            if token not in original_text and vault.lookup(token) is None:
                return token


def _extract_text_parts(messages: list[dict[str, Any]]) -> list[str]:
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return parts


def _merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for key, value in source.items():
        target[key] = target.get(key, 0) + value

