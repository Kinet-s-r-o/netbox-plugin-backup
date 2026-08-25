from .base import CredentialMaterial, SecretProvider, SecretProviderError
from .encrypted_database import EncryptedDatabaseSecretProvider
from .environment import EnvironmentSecretProvider
from .registry import SecretProviderRegistry
from .vault import VaultKV2SecretProvider

secret_provider_registry = SecretProviderRegistry()
secret_provider_registry.register(EnvironmentSecretProvider())
secret_provider_registry.register(EncryptedDatabaseSecretProvider())
secret_provider_registry.register(VaultKV2SecretProvider())

__all__ = [
    "CredentialMaterial",
    "EncryptedDatabaseSecretProvider",
    "EnvironmentSecretProvider",
    "SecretProvider",
    "SecretProviderError",
    "SecretProviderRegistry",
    "VaultKV2SecretProvider",
    "secret_provider_registry",
]
