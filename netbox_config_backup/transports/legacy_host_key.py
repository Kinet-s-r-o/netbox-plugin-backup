from __future__ import annotations

import asyncio

import asyncssh

from netbox_config_backup.drivers.base import DriverError

# Legacy appliances (for example SIAE SM-OS on OpenSSH 5.1) advertise only
# ssh-rsa/ssh-dss host keys together with CBC ciphers and HMAC-SHA1. These
# algorithms are opt-in in AsyncSSH and are enabled only for this pre-
# authentication identity read: no credentials are sent and no session is
# opened, so the weaker algorithms cannot expose secrets. Authentication
# always happens in the driver transport, never here.
_LEGACY_SERVER_HOST_KEY_ALGS = "+ssh-rsa,ssh-dss"
_LEGACY_KEX_ALGS = (
    "+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1,diffie-hellman-group-exchange-sha1"
)
_LEGACY_ENCRYPTION_ALGS = "+3des-cbc,aes128-cbc,aes192-cbc,aes256-cbc"
_LEGACY_MAC_ALGS = "+hmac-sha1,hmac-md5"


def legacy_scan_options() -> asyncssh.SSHClientConnectionOptions:
    return asyncssh.SSHClientConnectionOptions(
        server_host_key_algs=_LEGACY_SERVER_HOST_KEY_ALGS,
        kex_algs=_LEGACY_KEX_ALGS,
        encryption_algs=_LEGACY_ENCRYPTION_ALGS,
        mac_algs=_LEGACY_MAC_ALGS,
    )


def scan_legacy_host_key(address: str, port: int, timeout: int) -> tuple[str, str]:
    """Read an old appliance's SSH host key; returns ``(key_type, base64_key)``."""

    async def retrieve():
        return await asyncio.wait_for(
            asyncssh.get_server_host_key(
                address,
                port,
                options=legacy_scan_options(),
                config=None,
            ),
            timeout=timeout,
        )

    try:
        key = asyncio.run(retrieve())
        if key is None:
            raise DriverError("HOST_KEY_SCAN_FAILED", "The device did not present an SSH host key.")
        parts = key.export_public_key("openssh").decode().strip().split()
        if len(parts) < 2:
            raise DriverError("HOST_KEY_SCAN_FAILED", "The device presented an invalid SSH key.")
        return parts[0], parts[1]
    except DriverError:
        raise
    except TimeoutError as exc:
        raise DriverError(
            "TIMEOUT", "The SSH identity scan timed out before the device responded."
        ) from exc
    except asyncssh.KeyExchangeFailed as exc:
        raise DriverError(
            "SSH_NEGOTIATION_FAILED",
            "The device SSH algorithms could not be negotiated, so its identity was not read.",
        ) from exc
    except (asyncssh.Error, OSError, ValueError) as exc:
        raise DriverError(
            "HOST_KEY_SCAN_FAILED", "The device SSH identity could not be scanned safely."
        ) from exc
