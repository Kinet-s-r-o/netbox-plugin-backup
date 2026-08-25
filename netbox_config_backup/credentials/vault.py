from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from .base import CredentialMaterial, SecretProvider, SecretProviderError


class VaultKV2SecretProvider(SecretProvider):
    """Read device credentials from a HashiCorp Vault KV v2 mount."""

    provider_id = "vault_kv2"
    reference_scheme = "vault"
    _mount_pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        client_factory=None,
    ) -> None:
        self._config = config
        self._environ = os.environ if environ is None else environ
        self._client_factory = client_factory

    def resolve(self, reference: str) -> CredentialMaterial:
        config = self._settings()
        if not config.get("vault_enabled", False):
            raise SecretProviderError("Vault credential provider is disabled.")
        mount_point, path = self.parse_reference(reference)
        client = self._client(config)
        try:
            self._authenticate(client, config)
            response = client.secrets.kv.v2.read_secret_version(
                path=path,
                mount_point=mount_point,
                raise_on_deleted_version=True,
            )
            data = response["data"]["data"]
            if not isinstance(data, dict):
                raise TypeError("Vault KV response has no data object.")
            username = self._optional_string(data, "username")
            password = self._optional_string(data, "password")
            private_key = self._optional_string(data, "private_key")
            enable_secret = self._optional_string(data, "enable_secret")
            return CredentialMaterial(
                username=username or "",
                password=password,
                private_key=private_key,
                enable_secret=enable_secret,
            )
        except SecretProviderError:
            raise
        except Exception as exc:
            raise SecretProviderError() from exc

    def _settings(self) -> Mapping[str, Any]:
        if self._config is not None:
            return self._config
        from django.conf import settings

        return settings.PLUGINS_CONFIG["netbox_config_backup"]

    def _client(self, config: Mapping[str, Any]):
        address = str(config.get("vault_addr") or self._environ.get("VAULT_ADDR", ""))
        if not address:
            raise SecretProviderError("Vault address is not configured.")
        parsed = urlsplit(address)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SecretProviderError("Vault address is invalid.")
        if parsed.scheme != "https" and not config.get("vault_allow_insecure_http", False):
            raise SecretProviderError("Vault address must use HTTPS.")

        factory = self._client_factory
        if factory is None:
            try:
                import hvac
            except ModuleNotFoundError as exc:
                if exc.name != "hvac":
                    raise
                raise SecretProviderError(
                    "Vault credentials require the optional 'vault' package extra."
                ) from exc

            factory = hvac.Client
        verify: bool | str = config.get("vault_ca_bundle") or config.get("vault_verify_tls", True)
        namespace = str(config.get("vault_namespace") or self._environ.get("VAULT_NAMESPACE", ""))
        return factory(
            url=address,
            namespace=namespace or None,
            verify=verify,
            timeout=int(config.get("vault_timeout", 10)),
        )

    def _authenticate(self, client, config: Mapping[str, Any]) -> None:
        method = str(config.get("vault_auth_method", "token")).lower()
        if method == "token":
            token = self._environ.get("NETBOX_CONFIG_BACKUP_VAULT_TOKEN") or self._environ.get(
                "VAULT_TOKEN"
            )
            if not token:
                raise SecretProviderError("Vault token is not configured.")
            client.token = token
            return
        if method == "approle":
            role_id = self._environ.get("NETBOX_CONFIG_BACKUP_VAULT_ROLE_ID")
            secret_id = self._environ.get("NETBOX_CONFIG_BACKUP_VAULT_SECRET_ID")
            if not role_id or not secret_id:
                raise SecretProviderError("Vault AppRole credentials are not configured.")
            try:
                client.auth.approle.login(
                    role_id=role_id,
                    secret_id=secret_id,
                    mount_point=str(config.get("vault_auth_mount_point", "approle")),
                )
            except Exception as exc:
                raise SecretProviderError() from exc
            return
        raise SecretProviderError("Vault authentication method is invalid.")

    def parse_reference(self, reference: str) -> tuple[str, str]:
        if not isinstance(reference, str):
            raise SecretProviderError()
        parsed = urlsplit(reference)
        path = parsed.path.lstrip("/")
        if (
            parsed.scheme != self.reference_scheme
            or not self._mount_pattern.fullmatch(parsed.netloc)
            or not path
            or parsed.query
            or parsed.fragment
            or "\\" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
        ):
            raise SecretProviderError()
        return parsed.netloc, path

    @staticmethod
    def _optional_string(data: dict, key: str) -> str | None:
        value = data.get(key)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise TypeError("Vault credential values must be strings.")
        return value
