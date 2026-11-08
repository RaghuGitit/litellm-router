from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .detector import PrivacyDetector
from .masker import MaskingResult, PrivacyMasker
from .policy import PrivacyPolicy, load_policy
from .response_restorer import ResponseRestorer
from .token_vault import RequestVault, TokenVault

DEFAULT_POLICY_PATH = Path(__file__).parent / "policies.yaml"


class PrivacyService:
    def __init__(self, policy_path: str | Path | None = None) -> None:
        path = Path(policy_path or os.getenv("PRIVACY_POLICY_PATH", str(DEFAULT_POLICY_PATH)))
        self.policy: PrivacyPolicy = load_policy(path)
        self.detector = PrivacyDetector(self.policy)
        self.masker = PrivacyMasker(self.detector)
        self.restorer = ResponseRestorer()
        self.vault = TokenVault()

    def start_request(self, request_key: int) -> RequestVault:
        return self.vault.create(request_key)

    def mask_request_messages(self, messages: list[dict[str, Any]], request_vault: RequestVault) -> MaskingResult:
        return self.masker.mask_messages(messages, request_vault)

    def restore_response(self, response: Any, request_key: int) -> Any:
        request_vault = self.vault.get(request_key)
        if request_vault is None:
            return response
        return self.restorer.restore_response(response, request_vault)

    def cleanup(self, request_key: int) -> None:
        self.vault.discard(request_key)

