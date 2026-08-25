from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class SecretProviderError(RuntimeError):
    """A secret could not be resolved; the message must be safe to persist."""

    def __init__(self, safe_message: str = "Credential resolution failed.") -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True, repr=False)
class CredentialMaterial:
    username: str
    password: str | None = field(default=None, repr=False)
    private_key: str | None = field(default=None, repr=False)
    enable_secret: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.username:
            raise ValueError("username must not be empty")
        if bool(self.password) == bool(self.private_key):
            raise ValueError("Exactly one of password or private_key must be supplied.")

    def __repr__(self) -> str:
        auth_type = "password" if self.password is not None else "ssh_key"
        return (
            "CredentialMaterial("
            f"username={self.username!r}, auth_type={auth_type!r}, secrets=<redacted>)"
        )


class SecretProvider(ABC):
    provider_id: str

    @abstractmethod
    def resolve(self, reference: str) -> CredentialMaterial:
        """Resolve a reference without logging or persisting returned material."""
