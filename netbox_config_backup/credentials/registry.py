from __future__ import annotations

from .base import SecretProvider


class SecretProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, SecretProvider] = {}

    def register(self, provider: SecretProvider) -> SecretProvider:
        provider_id = getattr(provider, "provider_id", "")
        if not provider_id:
            raise ValueError("A secret provider must declare provider_id.")
        if provider_id in self._providers:
            raise ValueError(f"Secret provider {provider_id!r} is already registered.")
        self._providers[provider_id] = provider
        return provider

    def get(self, provider_id: str) -> SecretProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise SecretProviderLookupError(f"Unknown secret provider: {provider_id}") from exc

    def contains(self, provider_id: str) -> bool:
        return provider_id in self._providers


class SecretProviderLookupError(LookupError):
    pass
