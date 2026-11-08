from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class EntityPolicy:
    entity_type: str
    enabled: bool = True
    action: str = "tokenize"
    restore: bool = True
    score_threshold: float = 0.5


@dataclass(frozen=True)
class PatternPolicy:
    name: str
    entity_type: str
    regex: str
    score: float = 0.9
    enabled: bool = True
    action: str = "tokenize"
    restore: bool = True


@dataclass(frozen=True)
class PrivacyPolicy:
    version: str
    enabled: bool
    fail_closed: bool
    reject_streaming: bool
    entities: dict[str, EntityPolicy]
    patterns: tuple[PatternPolicy, ...]

    def policy_for(self, entity_type: str) -> EntityPolicy:
        return self.entities.get(
            entity_type,
            EntityPolicy(entity_type=entity_type),
        )


def _entity_policy(entity_type: str, raw: dict[str, Any]) -> EntityPolicy:
    return EntityPolicy(
        entity_type=entity_type,
        enabled=bool(raw.get("enabled", True)),
        action=str(raw.get("action", "tokenize")),
        restore=bool(raw.get("restore", True)),
        score_threshold=float(raw.get("score_threshold", 0.5)),
    )


def _pattern_policy(raw: dict[str, Any]) -> PatternPolicy:
    entity_type = str(raw["entity_type"])
    return PatternPolicy(
        name=str(raw.get("name", entity_type)),
        entity_type=entity_type,
        regex=str(raw["regex"]),
        score=float(raw.get("score", 0.9)),
        enabled=bool(raw.get("enabled", True)),
        action=str(raw.get("action", "tokenize")),
        restore=bool(raw.get("restore", True)),
    )


def load_policy(path: str | Path) -> PrivacyPolicy:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entities = {
        str(entity_type): _entity_policy(str(entity_type), raw or {})
        for entity_type, raw in (data.get("entities") or {}).items()
    }
    patterns = tuple(
        _pattern_policy(raw)
        for raw in (data.get("custom_patterns") or [])
        if raw and raw.get("enabled", True)
    )
    return PrivacyPolicy(
        version=str(data.get("version", "privacy-v1")),
        enabled=bool(data.get("enabled", True)),
        fail_closed=bool(data.get("fail_closed", True)),
        reject_streaming=bool(data.get("reject_streaming", True)),
        entities=entities,
        patterns=patterns,
    )

