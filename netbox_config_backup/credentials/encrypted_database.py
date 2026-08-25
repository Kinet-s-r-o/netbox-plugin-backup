from __future__ import annotations

import base64
import binascii
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .base import CredentialMaterial, SecretProvider, SecretProviderError

MASTER_KEY_ENV = "NETBOX_CONFIG_BACKUP_MASTER_KEY"
KEY_VERSION_ENV = "NETBOX_CONFIG_BACKUP_MASTER_KEY_VERSION"
PREVIOUS_KEYS_ENV = "NETBOX_CONFIG_BACKUP_PREVIOUS_MASTER_KEYS"
KEY_VERSION_PATTERN = re.compile(r"[A-Za-z0-9_.-]{1,50}")
MAX_PREVIOUS_KEYS = 16
MAX_KEYRING_ENV_LENGTH = 16 * 1024


class MasterKeyConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class EncryptedPayload:
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    key_version: str

    def __repr__(self) -> str:
        return f"EncryptedPayload(key_version={self.key_version!r}, data=<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class StoredCredentialData:
    username: str
    ciphertext: bytes = field(repr=False)
    nonce: bytes = field(repr=False)
    key_version: str

    def __repr__(self) -> str:
        return (
            "StoredCredentialData("
            f"username={self.username!r}, key_version={self.key_version!r}, data=<redacted>)"
        )


class CredentialLookup(Protocol):
    def __call__(self, reference: UUID) -> StoredCredentialData: ...


class DatabaseCredentialCipher:
    nonce_size = 12

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        nonce_factory: Callable[[int], bytes] = os.urandom,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._nonce_factory = nonce_factory

    def encrypt(self, *, reference: UUID, plaintext: str) -> EncryptedPayload:
        if not plaintext:
            raise ValueError("Password must not be empty.")
        key, version = self.active_key()
        nonce = self._nonce_factory(self.nonce_size)
        if len(nonce) != self.nonce_size:
            raise ValueError("Nonce factory returned an invalid nonce.")
        ciphertext = AESGCM(key).encrypt(
            nonce,
            plaintext.encode("utf-8"),
            self._aad(reference, version),
        )
        return EncryptedPayload(
            ciphertext=ciphertext,
            nonce=nonce,
            key_version=version,
        )

    def decrypt(
        self,
        *,
        reference: UUID,
        ciphertext: bytes,
        nonce: bytes,
        key_version: str,
    ) -> str:
        key = self.key_for_version(key_version)
        try:
            plaintext = AESGCM(key).decrypt(
                bytes(nonce),
                bytes(ciphertext),
                self._aad(reference, key_version),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, UnicodeError, ValueError) as exc:
            raise MasterKeyConfigurationError("Credential decryption failed.") from exc

    def active_key(self) -> tuple[bytes, str]:
        encoded = self._environ.get(MASTER_KEY_ENV, "").strip()
        version = self._environ.get(KEY_VERSION_ENV, "1").strip()
        if not encoded or not version or not KEY_VERSION_PATTERN.fullmatch(version):
            raise MasterKeyConfigurationError("Encrypted credential master key is not configured.")
        return self._decode_key(encoded), version

    def key_for_version(self, version: str) -> bytes:
        try:
            return self.keyring()[version]
        except KeyError as exc:
            raise MasterKeyConfigurationError("Credential key version is not configured.") from exc

    def configured_key_versions(self) -> tuple[str, ...]:
        active_version = self.active_key()[1]
        return (active_version, *(v for v in self.keyring() if v != active_version))

    def keyring(self) -> dict[str, bytes]:
        active_key, active_version = self.active_key()
        previous = self._previous_keys()
        if active_version in previous:
            raise MasterKeyConfigurationError(
                "The active master key version must not also be configured as previous."
            )
        keyring = {active_version: active_key, **previous}
        if len(set(keyring.values())) != len(keyring):
            raise MasterKeyConfigurationError(
                "Each master key version must use distinct key material."
            )
        return keyring

    def _previous_keys(self) -> dict[str, bytes]:
        raw = self._environ.get(PREVIOUS_KEYS_ENV, "").strip()
        if not raw:
            return {}
        if len(raw) > MAX_KEYRING_ENV_LENGTH:
            raise MasterKeyConfigurationError("Previous master key configuration is invalid.")
        try:
            pairs = json.loads(raw, object_pairs_hook=self._unique_json_object)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise MasterKeyConfigurationError(
                "Previous master key configuration is invalid."
            ) from exc
        if not isinstance(pairs, dict) or len(pairs) > MAX_PREVIOUS_KEYS:
            raise MasterKeyConfigurationError("Previous master key configuration is invalid.")
        previous: dict[str, bytes] = {}
        for version, encoded in pairs.items():
            if (
                not isinstance(version, str)
                or not KEY_VERSION_PATTERN.fullmatch(version)
                or not isinstance(encoded, str)
            ):
                raise MasterKeyConfigurationError("Previous master key configuration is invalid.")
            previous[version] = self._decode_key(encoded)
        return previous

    @staticmethod
    def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    @staticmethod
    def _decode_key(encoded: str) -> bytes:
        try:
            padded = encoded + "=" * (-len(encoded) % 4)
            key = base64.b64decode(padded, altchars=b"-_", validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MasterKeyConfigurationError(
                "Encrypted credential master key is invalid."
            ) from exc
        if len(key) != 32:
            raise MasterKeyConfigurationError(
                "Encrypted credential master key must decode to 32 bytes."
            )
        return key

    @staticmethod
    def _aad(reference: UUID, key_version: str) -> bytes:
        return f"netbox-config-backup:{reference}:{key_version}".encode()


class EncryptedDatabaseSecretProvider(SecretProvider):
    provider_id = "encrypted_database"
    reference_prefix = "db://"

    def __init__(
        self,
        *,
        cipher: DatabaseCredentialCipher | None = None,
        lookup: CredentialLookup | None = None,
    ) -> None:
        self._cipher = cipher or DatabaseCredentialCipher()
        self._lookup = lookup or self._django_lookup

    def resolve(self, reference: str) -> CredentialMaterial:
        try:
            reference_id = self.parse_reference(reference)
            stored = self._lookup(reference_id)
            password = self._cipher.decrypt(
                reference=reference_id,
                ciphertext=stored.ciphertext,
                nonce=stored.nonce,
                key_version=stored.key_version,
            )
            return CredentialMaterial(username=stored.username, password=password)
        except Exception as exc:
            if isinstance(exc, SecretProviderError):
                raise
            raise SecretProviderError() from exc

    @classmethod
    def parse_reference(cls, reference: str) -> UUID:
        if not isinstance(reference, str) or not reference.startswith(cls.reference_prefix):
            raise SecretProviderError()
        try:
            return UUID(reference.removeprefix(cls.reference_prefix))
        except (ValueError, AttributeError) as exc:
            raise SecretProviderError() from exc

    @staticmethod
    def format_reference(reference: UUID) -> str:
        return f"db://{reference}"

    @staticmethod
    def _django_lookup(reference: UUID) -> StoredCredentialData:
        from netbox_config_backup.models import StoredCredential

        stored = StoredCredential.objects.only(
            "username", "ciphertext", "nonce", "key_version"
        ).get(reference=reference)
        return StoredCredentialData(
            username=stored.username,
            ciphertext=bytes(stored.ciphertext),
            nonce=bytes(stored.nonce),
            key_version=stored.key_version,
        )
