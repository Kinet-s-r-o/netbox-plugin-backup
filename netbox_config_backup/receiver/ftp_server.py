from __future__ import annotations

import logging
import re
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from netbox_config_backup.credentials.base import CredentialMaterial

from .server import prepare_receiver_root

logger = logging.getLogger("netbox_config_backup.receiver")
LEGACY_ALFOPLUS_FILENAME = re.compile(r"^ncb-[0-9]+-[a-f0-9]{16}\.bak$")


class UploadOnlyFTPHandler(FTPHandler):
    """Legacy, upload-only FTP handler restricted to one receiver inbox."""

    allowed_inbox: Path

    def on_connect(self) -> None:
        logger.info("Legacy FTP receiver connection opened from %s.", self.remote_ip)

    def on_login(self, _username: str) -> None:
        logger.info("Legacy FTP receiver authentication succeeded.")

    def on_login_failed(self, _username: str, _password: str) -> None:
        logger.info("Legacy FTP receiver authentication failed.")

    def on_file_received(self, _file: str) -> None:
        logger.info("Legacy FTP receiver accepted an uploaded file.")

    def ftp_STOR(self, file: str, mode: str = "w"):
        # pyftpdlib resolves the FTP path into the chrooted filesystem before
        # dispatching this method.
        destination = Path(file).resolve(strict=False)
        inbox = self.allowed_inbox.resolve(strict=True)
        # ALFOplus sends the Windows path accepted by WebLCT as one FTP path.
        # Map only its basename into the chrooted inbox.
        if destination.parent == inbox.parent and "\\" in destination.name:
            destination = inbox / destination.name.rsplit("\\", 1)[-1]
            file = str(destination)
        if destination.parent != inbox or destination.name in {"", ".", ".."}:
            self.respond("550 Uploads are accepted only in the configured inbox.")
            return None
        if not LEGACY_ALFOPLUS_FILENAME.fullmatch(destination.name):
            self.respond("550 The upload filename was not assigned by Config Backup.")
            return None
        if destination.exists() or destination.is_symlink():
            self.respond("550 Refusing to overwrite an existing upload.")
            return None
        return super().ftp_STOR(file, mode)

    def ftp_APPE(self, _file: str):
        self.respond("550 Appending to uploads is disabled.")

    def ftp_STOU(self, _line: str):
        self.respond("550 Unique-name uploads are disabled; use the assigned inbox path.")

    def ftp_RETR(self, _file: str):
        self.respond("550 This endpoint accepts uploads only.")

    def ftp_DELE(self, _path: str):
        self.respond("550 Deleting files is disabled.")

    def ftp_RNFR(self, _path: str):
        self.respond("550 Renaming files is disabled.")

    def ftp_MKD(self, _path: str):
        self.respond("550 Creating directories is disabled.")

    def ftp_RMD(self, _path: str):
        self.respond("550 Removing directories is disabled.")


def run_ftp_receiver(
    *,
    listen_host: str,
    listen_port: int,
    advertised_host: str,
    profile_root: Path,
    upload_directory: str,
    passive_port_start: int,
    passive_port_end: int,
    credentials: CredentialMaterial,
) -> None:
    if credentials.private_key or not credentials.password:
        raise ValueError("The legacy FTP receiver requires password credentials.")
    if passive_port_start > passive_port_end or passive_port_end - passive_port_start > 99:
        raise ValueError("The legacy FTP passive port range is invalid.")

    inbox = prepare_receiver_root(profile_root, upload_directory)
    authorizer = DummyAuthorizer()
    # `e` permits CWD into the pre-created inbox; `w` permits STOR. No read,
    # listing, delete, rename, or directory-creation permission is granted.
    authorizer.add_user(
        credentials.username,
        credentials.password,
        str(profile_root),
        perm="ew",
    )

    handler = type("ConfiguredUploadOnlyFTPHandler", (UploadOnlyFTPHandler,), {})
    handler.authorizer = authorizer
    handler.allowed_inbox = inbox
    handler.passive_ports = range(passive_port_start, passive_port_end + 1)
    handler.masquerade_address = advertised_host or None
    handler.banner = "Config Backup legacy upload receiver"
    handler.permit_foreign_addresses = False
    handler.permit_privileged_ports = False

    server = FTPServer((listen_host, listen_port), handler)
    server.max_cons = 32
    server.max_cons_per_ip = 4
    logger.warning(
        "Starting plaintext legacy FTP receiver on %s:%s; use only on a trusted management network.",
        listen_host,
        listen_port,
    )
    try:
        server.serve_forever(timeout=1.0, blocking=True, handle_exit=True)
    finally:
        server.close_all()
