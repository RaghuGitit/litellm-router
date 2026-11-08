from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TokenRecord:
    token: str
    original: str
    entity_type: str
    restore: bool


@dataclass
class RequestVault:
    token_to_record: dict[str, TokenRecord] = field(default_factory=dict)
    value_to_token: dict[tuple[str, str], str] = field(default_factory=dict)

    def register(self, token: str, original: str, entity_type: str, restore: bool) -> None:
        record = TokenRecord(
            token=token,
            original=original,
            entity_type=entity_type,
            restore=restore,
        )
        self.token_to_record[token] = record
        self.value_to_token[(entity_type, original)] = token

    def token_for_value(self, entity_type: str, original: str) -> str | None:
        return self.value_to_token.get((entity_type, original))

    def lookup(self, token: str) -> TokenRecord | None:
        return self.token_to_record.get(token)


class TokenVault:
    def __init__(self) -> None:
        self._vaults: dict[int, RequestVault] = {}

    def create(self, request_key: int) -> RequestVault:
        vault = RequestVault()
        self._vaults[request_key] = vault
        return vault

    def get(self, request_key: int) -> RequestVault | None:
        return self._vaults.get(request_key)

    def discard(self, request_key: int) -> None:
        self._vaults.pop(request_key, None)

