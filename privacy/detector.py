from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from .policy import PatternPolicy, PrivacyPolicy

log = logging.getLogger("privacy.detector")


@dataclass(frozen=True)
class Detection:
    start: int
    end: int
    entity_type: str
    score: float
    action: str
    restore: bool

    @property
    def length(self) -> int:
        return self.end - self.start


FALLBACK_PATTERNS: tuple[PatternPolicy, ...] = (
    PatternPolicy(
        name="email",
        entity_type="EMAIL_ADDRESS",
        regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        score=0.95,
    ),
    PatternPolicy(
        name="phone",
        entity_type="PHONE_NUMBER",
        regex=r"(?<!\w)(?:\+?\d[\d .()\-]{7,}\d)(?!\w)",
        score=0.85,
    ),
    PatternPolicy(
        name="iso_or_slash_date",
        entity_type="DATE_TIME",
        regex=r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
        score=0.8,
    ),
    PatternPolicy(
        name="api_key",
        entity_type="API_KEY",
        regex=r"\b(?:sk-[A-Za-z0-9_\-]{20,}|(?:ghp|github_pat|xox[baprs])-[A-Za-z0-9_\-]{20,})\b",
        score=0.95,
        action="redact",
        restore=False,
    ),
    PatternPolicy(
        name="private_key_block",
        entity_type="PRIVATE_KEY",
        regex=r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----",
        score=0.99,
        action="redact",
        restore=False,
    ),
)


class PrivacyDetector:
    def __init__(self, policy: PrivacyPolicy):
        self.policy = policy
        self._analyzer = self._build_presidio_analyzer()

    def detect(self, text: str) -> list[Detection]:
        if not text:
            return []
        detections = []
        detections.extend(self._detect_with_presidio(text))
        detections.extend(self._detect_with_patterns(text, FALLBACK_PATTERNS))
        detections.extend(self._detect_with_patterns(text, self.policy.patterns))
        return self._resolve_overlaps(detections)

    def _build_presidio_analyzer(self):
        try:
            from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
            from presidio_analyzer.nlp_engine import NoOpNlpEngine

            nlp_engine = NoOpNlpEngine(models=[{"lang_code": "en", "model_name": "noop"}])
            registry = RecognizerRegistry()
            registry.load_predefined_recognizers(nlp_engine=nlp_engine, languages=["en"])
            for pattern in self.policy.patterns:
                registry.add_recognizer(
                    PatternRecognizer(
                        supported_entity=pattern.entity_type,
                        patterns=[Pattern(pattern.name, pattern.regex, pattern.score)],
                    )
                )
            return AnalyzerEngine(
                registry=registry,
                nlp_engine=nlp_engine,
                supported_languages=["en"],
            )
        except Exception as exc:
            log.warning("Presidio analyzer unavailable; using local pattern recognizers only: %s", type(exc).__name__)
            return None

    def _detect_with_presidio(self, text: str) -> list[Detection]:
        if self._analyzer is None:
            return []
        supported_entities = set(self._analyzer.get_supported_entities(language="en"))
        entity_names = [
            entity_type
            for entity_type, policy in self.policy.entities.items()
            if policy.enabled and entity_type in supported_entities
        ]
        if not entity_names:
            return []
        try:
            results = self._analyzer.analyze(
                text=text,
                language="en",
                entities=entity_names,
                score_threshold=min(
                    (policy.score_threshold for policy in self.policy.entities.values() if policy.enabled),
                    default=0.5,
                ),
            )
        except Exception as exc:
            log.warning("Presidio detection failed without prompt content: %s", type(exc).__name__)
            raise
        detections = []
        for result in results:
            policy = self.policy.policy_for(result.entity_type)
            if not policy.enabled or result.score < policy.score_threshold:
                continue
            detections.append(
                Detection(
                    start=int(result.start),
                    end=int(result.end),
                    entity_type=result.entity_type,
                    score=float(result.score),
                    action=policy.action,
                    restore=policy.restore,
                )
            )
        return detections

    def _detect_with_patterns(self, text: str, patterns: Iterable[PatternPolicy]) -> list[Detection]:
        detections = []
        for pattern in patterns:
            if not pattern.enabled:
                continue
            policy = self.policy.policy_for(pattern.entity_type)
            action = pattern.action or policy.action
            restore = pattern.restore if pattern.action else policy.restore
            threshold = policy.score_threshold
            if pattern.score < threshold:
                continue
            for match in re.finditer(pattern.regex, text, flags=re.IGNORECASE):
                detections.append(
                    Detection(
                        start=match.start(),
                        end=match.end(),
                        entity_type=pattern.entity_type,
                        score=pattern.score,
                        action=action,
                        restore=restore,
                    )
                )
        return detections

    @staticmethod
    def _resolve_overlaps(detections: list[Detection]) -> list[Detection]:
        accepted: list[Detection] = []
        occupied: list[range] = []
        for detection in sorted(
            detections,
            key=lambda item: (-item.score, -item.length, item.start, item.entity_type),
        ):
            span = range(detection.start, detection.end)
            if any(detection.start < used.stop and detection.end > used.start for used in occupied):
                continue
            accepted.append(detection)
            occupied.append(span)
        return sorted(accepted, key=lambda item: item.start)

