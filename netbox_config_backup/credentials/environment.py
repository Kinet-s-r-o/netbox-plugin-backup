from __future__ import annotations

import os
import re
from collections.abc import Mapping

from .base import CredentialMaterial, SecretProvider, SecretProviderError


class EnvironmentSecretProvider(SecretProvider):
    """Resolve credential material from process environment variables.

    A reference such as ``env://ROUTER_1`` resolves the following variables:

    - ``ROUTER_1_USERNAME`` (required)
    - exactly one of ``ROUTER_1_PASSWORD`` or ``ROUTER_1_PRIVATE_KEY``
    - ``ROUTER_1_ENABLE_SECRET`` (optional)

    Error messages are intentionally generic so neither values nor useful pieces
    of a malformed reference can be persisted in a backup run.
    """

    provider_id = "environment"
    reference_prefix = "env://"
    _name_pattern = re.compile(r"[A-Z][A-Z0-9_]*\Z")

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = os.environ if environ is None else environ

    def resolve(self, reference: str) -> CredentialMaterial:
        name = self._parse_reference(reference)
        username = self._read(f"{name}_USERNAME")
        password = self._read(f"{name}_PASSWORD")
        private_key = self._read(f"{name}_PRIVATE_KEY")
        enable_secret = self._read(f"{name}_ENABLE_SECRET")

        if not username:
            raise SecretProviderError()
        if bool(password) == bool(private_key):
            raise SecretProviderError()

        try:
            return CredentialMaterial(
                username=username,
                password=password,
                private_key=private_key,
                enable_secret=enable_secret,
            )
        except ValueError as exc:
            raise SecretProviderError() from exc

    def _parse_reference(self, reference: str) -> str:
        if not isinstance(reference, str) or not reference.startswith(self.reference_prefix):
            raise SecretProviderError()
        name = reference.removeprefix(self.reference_prefix)
        if not self._name_pattern.fullmatch(name):
            raise SecretProviderError()
        return name

    def _read(self, variable: str) -> str | None:
        value = self._environ.get(variable)
        return value if value else None
