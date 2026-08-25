from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from netbox_config_backup.drivers.base import ConnectionParameters, DriverError

MAX_KNOWN_HOSTS_BYTES = 5 * 1024 * 1024


@contextmanager
def materialized_known_hosts(connection: ConnectionParameters):
    """Yield one known_hosts file combining deployment and database trust stores."""
    trusted_lines = tuple(connection.trusted_host_keys)
    if not trusted_lines:
        yield connection.known_hosts_path
        return

    lines: list[str] = []
    source = connection.known_hosts_path
    if source:
        try:
            path = Path(source)
            if path.exists():
                if path.stat().st_size > MAX_KNOWN_HOSTS_BYTES:
                    raise DriverError(
                        "KNOWN_HOSTS_INVALID", "The configured known_hosts file is too large."
                    )
                lines.extend(path.read_text(encoding="utf-8").splitlines())
        except DriverError:
            raise
        except OSError as exc:
            raise DriverError(
                "KNOWN_HOSTS_INVALID", "The configured known_hosts file could not be read."
            ) from exc

    for line in trusted_lines:
        if not line or len(line) > 16384 or any(value in line for value in ("\r", "\n", "\x00")):
            raise DriverError("KNOWN_HOSTS_INVALID", "A trusted SSH host key is invalid.")
        lines.append(line)

    descriptor, temporary_path = tempfile.mkstemp(prefix="ncb-known-hosts-", text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(dict.fromkeys(lines)))
            handle.write("\n")
        try:
            os.chmod(temporary_path, 0o600)
        except OSError:
            pass
        yield temporary_path
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
