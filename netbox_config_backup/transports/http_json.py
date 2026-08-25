from __future__ import annotations

import json
import ssl
from collections.abc import Mapping
from http.client import HTTPResponse
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from netbox_config_backup.drivers.base import DriverError


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpJsonTransport:
    """Bounded HTTPS JSON transport which never follows redirects."""

    absolute_max_response_bytes = 100 * 1024 * 1024

    def post_json(
        self,
        url: str,
        payload: Mapping[str, Any],
        *,
        headers: Mapping[str, str] | None = None,
        timeout: int = 30,
        verify_tls: bool = True,
        ca_bundle_path: str = "",
        max_response_bytes: int = 20 * 1024 * 1024,
    ) -> Any:
        if max_response_bytes <= 0 or max_response_bytes > self.absolute_max_response_bytes:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS",
                "HTTP response size limit is outside the allowed range.",
            )
        context = self._ssl_context(verify_tls=verify_tls, ca_bundle_path=ca_bundle_path)
        opener = build_opener(HTTPSHandler(context=context), _RejectRedirects())
        request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        request_headers.update(headers or {})
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = self._read_bounded(response, max_response_bytes)
        except HTTPError as exc:
            if exc.code in {400, 401, 403}:
                raise DriverError("AUTH_FAILED", "Device API authentication failed.") from exc
            if 300 <= exc.code < 400:
                raise DriverError(
                    "HTTP_REDIRECT", "Device API returned an unexpected redirect."
                ) from exc
            raise DriverError(
                "HTTP_FAILED", f"Device API returned HTTP status {exc.code}."
            ) from exc
        except TimeoutError as exc:
            raise DriverError("TIMEOUT", "The device HTTPS request timed out.") from exc
        except ssl.SSLCertVerificationError as exc:
            raise DriverError(
                "TLS_VERIFY_FAILED", "Device TLS certificate verification failed."
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLCertVerificationError):
                raise DriverError(
                    "TLS_VERIFY_FAILED", "Device TLS certificate verification failed."
                ) from exc
            if isinstance(exc.reason, TimeoutError):
                raise DriverError("TIMEOUT", "The device HTTPS request timed out.") from exc
            raise DriverError("CONNECTION_FAILED", "The device HTTPS connection failed.") from exc
        except OSError as exc:
            raise DriverError("CONNECTION_FAILED", "The device HTTPS connection failed.") from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DriverError("INVALID_OUTPUT", "Device API returned invalid JSON.") from exc

    @staticmethod
    def _ssl_context(*, verify_tls: bool, ca_bundle_path: str) -> ssl.SSLContext:
        try:
            if verify_tls:
                return ssl.create_default_context(cafile=ca_bundle_path or None)
            return ssl._create_unverified_context()
        except (OSError, ssl.SSLError) as exc:
            raise DriverError(
                "TLS_CONFIGURATION_FAILED", "TLS trust configuration is invalid."
            ) from exc

    @staticmethod
    def _read_bounded(response: HTTPResponse, limit: int) -> bytes:
        declared = response.headers.get("Content-Length")
        if declared:
            try:
                if int(declared) > limit:
                    raise DriverError("CONFIG_TOO_LARGE", "Device API response is too large.")
            except ValueError:
                pass
        content = response.read(limit + 1)
        if len(content) > limit:
            raise DriverError("CONFIG_TOO_LARGE", "Device API response is too large.")
        return content
