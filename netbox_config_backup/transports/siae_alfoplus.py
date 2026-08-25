from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from uuid import uuid4

from netbox_config_backup.drivers.base import DriverContext, DriverError


class SiaeAlfoplusWebLctTransport:
    """Receive a legacy ALFOplus native backup through its WebLCT/FTP workflow.

    Only WebLCT backup action ``1`` is issued. Restore/revert actions are never
    exposed. FTP is a device limitation and must be isolated to a trusted
    management network.
    """

    _STATUS_COMPLETED = 1
    _STATUS_INTERRUPTED = 2
    _STATUS_IDLE = 0
    _BACKUP_ACTION = 1
    _ABORT_ACTION = 4

    def __init__(self, *, opener=None, clock=time.monotonic, sleep=time.sleep) -> None:
        self._opener = opener or build_opener()
        self._clock = clock
        self._sleep = sleep

    def collect(self, context: DriverContext, *, options: dict) -> bytes:
        self._validate(context, options)
        receiver = context.receiver
        assert receiver is not None

        filename = f"ncb-{context.device_id}-{uuid4().hex[:16]}.bak"
        # This WebLCT generation validates a Windows-style path because the
        # original SCT/WebLCT Console FTP helper wrote into a local PC folder.
        remote_path = f"C:\\{receiver.upload_directory}\\{filename}"
        inbox = Path(receiver.inbox_path)
        artifact_path = inbox / filename
        inbox.mkdir(parents=True, exist_ok=True, mode=0o700)
        if inbox.is_symlink() or artifact_path.is_symlink():
            raise DriverError("RECEIVER_PATH_UNSAFE", "The receiver inbox path is unsafe.")
        artifact_path.unlink(missing_ok=True)

        web_port = options.get("web_port", 80)
        base_url = f"http://{context.address}:{web_port}"
        username = context.credentials.username.upper()
        password = (context.credentials.password or "").upper()
        logged_in = False
        backup_started = False
        original_ftp_mode = None
        ftp_mode_oid = None

        try:
            current_session = self._post(base_url, "/Snmp.LogInfo", [], context)
            if self._is_foreign_active_session(
                current_session,
                username=username,
                expected_address=receiver.advertised_host,
            ):
                raise DriverError(
                    "WEBLCT_SESSION_BUSY",
                    (
                        "ALFOplus is being managed by another WebLCT client. The plugin will "
                        "not terminate that session; close it and retry the backup."
                    ),
                )
            login_result = self._post(base_url, "/Snmp.Login", [username, password], context)
            self._check_login(login_result)
            logged_in = True

            login_info = self._post(base_url, "/Snmp.LogInfo", [], context)
            self._check_backup_role(login_info)
            device_seen_address = str(login_info.get("UserIp", "")).strip()
            if device_seen_address and device_seen_address != receiver.advertised_host:
                raise DriverError(
                    "RECEIVER_ADDRESS_MISMATCH",
                    (
                        "ALFOplus sees the backup worker at a different address than the "
                        "legacy FTP receiver. Set the receiver advertised address to the "
                        "worker address visible from the radio."
                    ),
                )

            snmp_version = int(self._snmp_get(base_url, "1.4.1.3373.206.7.0", context))
            ftp_mode_oid = self._ftp_mode_oid(
                snmp_version=snmp_version,
                user_ip=device_seen_address,
                username=username,
            )
            original_ftp_mode = int(self._snmp_get(base_url, ftp_mode_oid, context))
            if original_ftp_mode != 1:
                if options.get("allow_legacy_ftp_setup") is not True:
                    raise DriverError(
                        "LEGACY_FTP_SETUP_REQUIRED",
                        (
                            "ALFOplus file transfer is not set to FTP for this WebLCT user. "
                            "Enable the explicit legacy FTP setup option or select FTP once "
                            "in the device Security Configuration."
                        ),
                    )
                self._snmp_set(
                    base_url,
                    ftp_mode_oid,
                    1,
                    2,
                    context,
                    error_code="FTP_MODE_REJECTED",
                    safe_message="ALFOplus rejected switching this WebLCT session to FTP mode.",
                )

            if options.get("sync_receiver_credentials") is True:
                receiver_credentials = receiver.credentials
                assert receiver_credentials is not None
                self._snmp_set_many(
                    base_url,
                    [".5.3.0", ".5.4.0"],
                    [
                        receiver_credentials.username.upper(),
                        (receiver_credentials.password or "").upper(),
                    ],
                    [4, 4],
                    context,
                    error_code="RECEIVER_CREDENTIAL_SYNC_REJECTED",
                    safe_message=(
                        "ALFOplus rejected the dedicated FTP receiver credentials. Check that "
                        "the WebLCT account is SYSTEM or Station Operator."
                    ),
                )

            self._snmp_set(
                base_url,
                ".30.1.0",
                remote_path,
                4,
                context,
                error_code="BACKUP_PATH_REJECTED",
                safe_message="ALFOplus rejected the native backup destination path.",
            )
            self._snmp_set(
                base_url,
                ".30.2.0",
                self._BACKUP_ACTION,
                2,
                context,
                error_code="BACKUP_ACTION_REJECTED",
                safe_message="ALFOplus rejected starting the native backup action.",
            )
            backup_started = True
            return self._wait_for_backup(
                base_url,
                artifact_path,
                timeout=receiver.export_timeout,
                max_bytes=receiver.max_upload_bytes,
                context=context,
            )
        finally:
            if backup_started and not artifact_path.exists():
                try:
                    self._snmp_set(base_url, ".30.2.0", self._ABORT_ACTION, 2, context)
                except Exception:  # noqa: BLE001, S110
                    pass
            if ftp_mode_oid and original_ftp_mode not in {None, 1}:
                try:
                    self._snmp_set(base_url, ftp_mode_oid, original_ftp_mode, 2, context)
                except Exception:  # noqa: BLE001, S110
                    pass
            if logged_in:
                try:
                    self._post(base_url, "/Snmp.Logout", [username], context)
                except Exception:  # noqa: BLE001, S110
                    pass
            artifact_path.unlink(missing_ok=True)

    def _wait_for_backup(
        self,
        base_url: str,
        artifact_path: Path,
        *,
        timeout: int,
        max_bytes: int,
        context: DriverContext,
    ) -> bytes:
        deadline = self._clock() + timeout
        previous_size = -1
        stable_checks = 0
        completed = False
        while self._clock() < deadline:
            status = int(self._snmp_get(base_url, ".30.3.0", context))
            if status == self._STATUS_INTERRUPTED:
                failure = self._snmp_get(base_url, ".30.4.0", context)
                raise DriverError(
                    "DEVICE_EXPORT_FAILED",
                    self._failure_message(failure),
                )
            completed = completed or status == self._STATUS_COMPLETED
            try:
                size = artifact_path.stat().st_size
            except FileNotFoundError:
                self._sleep(0.5)
                continue
            if size > max_bytes:
                raise DriverError("CONFIG_TOO_LARGE", "The native backup file is too large.")
            if completed and size > 0 and size == previous_size:
                stable_checks += 1
                if stable_checks >= 2:
                    content = artifact_path.read_bytes()
                    if len(content) == size:
                        return content
                    stable_checks = 0
            else:
                previous_size = size
                stable_checks = 0
            self._sleep(0.5)
        raise DriverError(
            "RECEIVER_UPLOAD_TIMEOUT",
            (
                "ALFOplus did not finish the native backup upload. Check that the legacy FTP "
                "receiver is reachable on port 21 and that its credentials match the radio "
                "file-transfer credentials."
            ),
        )

    def _post(self, base_url: str, path: str, payload, context: DriverContext) -> dict:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            base_url + path,
            data=body,
            headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=context.connection.connect_timeout) as response:
                raw = response.read(1024 * 1024)
        except HTTPError as exc:
            raise DriverError(
                "WEBLCT_HTTP_ERROR", "ALFOplus WebLCT rejected the backup request."
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise DriverError(
                "WEBLCT_UNREACHABLE",
                "ALFOplus WebLCT is not reachable from the backup worker over HTTP.",
            ) from exc
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned an invalid response."
            ) from exc
        if not isinstance(result, dict):
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned an invalid response."
            )
        return result

    def _snmp_get(self, base_url: str, oid: str, context: DriverContext):
        result = self._post(base_url, "/Snmp.Get", [oid], context)
        self._check_snmp_result(result)
        values = [value for key, value in result.items() if key not in {"Err", "Indx"}]
        if not values:
            raise DriverError("WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned no value.")
        return values[0]

    def _snmp_set(
        self,
        base_url,
        oid,
        value,
        value_type,
        context,
        *,
        error_code="DEVICE_EXPORT_REJECTED",
        safe_message="ALFOplus rejected the native backup operation.",
    ) -> None:
        self._snmp_set_many(
            base_url,
            [oid],
            [value],
            [value_type],
            context,
            error_code=error_code,
            safe_message=safe_message,
        )

    def _snmp_set_many(
        self,
        base_url,
        oids,
        values,
        value_types,
        context,
        *,
        error_code="DEVICE_EXPORT_REJECTED",
        safe_message="ALFOplus rejected the native backup operation.",
    ) -> None:
        # The legacy endpoint accepts this JavaScript-object notation rather than
        # strict JSON. Its BuildJsonArray() quotes every value but does not escape
        # Windows path separators, so match the vendor client exactly.
        body = (
            "{o:"
            + self._legacy_array(oids)
            + ",v:"
            + self._legacy_array(values)
            + ",t:"
            + self._legacy_array(value_types)
            + "}"
        ).encode("utf-8")
        request = Request(
            base_url + "/Snmp.Set",
            data=body,
            headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=context.connection.connect_timeout) as response:
                raw = response.read(1024 * 1024)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise DriverError(
                "WEBLCT_UNREACHABLE", "ALFOplus WebLCT did not accept the backup request."
            ) from exc
        result = self._decode_set_response(raw)
        self._check_snmp_result(
            result,
            error_code=error_code,
            safe_message=safe_message,
        )

    @staticmethod
    def _decode_set_response(raw: bytes) -> dict:
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            # Successful Windows-path writes are echoed with unescaped
            # backslashes, making the otherwise JSON-like response invalid.
            text = raw.decode("utf-8", errors="replace")
            error_match = re.search(r'"Err"\s*:\s*"?(-?[0-9]+)"?', text)
            index_match = re.search(r'"Indx"\s*:\s*"?(-?[0-9]+)"?', text)
            if error_match is None:
                raise DriverError(
                    "WEBLCT_INVALID_RESPONSE",
                    "ALFOplus WebLCT returned an invalid response.",
                )
            result = {
                "Err": error_match.group(1),
                "Indx": index_match.group(1) if index_match else "0",
            }
        if not isinstance(result, dict):
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned an invalid response."
            )
        return result

    @staticmethod
    def _legacy_array(values) -> str:
        encoded = []
        for value in values:
            text = str(value)
            if any(character in text for character in ('"', "\r", "\n", "\x00")):
                raise DriverError(
                    "INVALID_DRIVER_OPTIONS",
                    "A legacy ALFOplus WebLCT value contains an unsafe character.",
                )
            encoded.append(f'"{text}"')
        return "[" + ",".join(encoded) + "]"

    @staticmethod
    def _check_snmp_result(
        result: dict,
        *,
        error_code="DEVICE_EXPORT_REJECTED",
        safe_message="ALFOplus rejected the native backup operation or the user lacks permission.",
    ) -> None:
        try:
            error = int(result.get("Err", 0))
        except (TypeError, ValueError) as exc:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned an invalid result code."
            ) from exc
        if error:
            raise DriverError(
                error_code,
                f"{safe_message} Device result code: {error}.",
            )

    @staticmethod
    def _check_login(result: dict) -> None:
        try:
            status = int(result.get("Status", -1))
        except (TypeError, ValueError) as exc:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus WebLCT returned an invalid login result."
            ) from exc
        if status in {0, 128}:
            return
        messages = {
            129: ("WEB_SESSION_LIMIT", "ALFOplus has no free WebLCT session."),
            130: ("AUTH_FAILED", "ALFOplus WebLCT authentication failed."),
            5: ("AUTH_FAILED", "ALFOplus WebLCT authentication failed."),
            131: (
                "DEVICE_UPDATE_PROTECTED",
                "ALFOplus is currently protected by another element manager session.",
            ),
            132: (
                "WEBLCT_SYSTEM_REQUIRED",
                "ALFOplus currently permits a new WebLCT login only for a SYSTEM user.",
            ),
        }
        code, message = messages.get(
            status,
            ("WEBLCT_LOGIN_FAILED", "ALFOplus WebLCT login failed."),
        )
        raise DriverError(code, message)

    @staticmethod
    def _check_backup_role(login_info: dict) -> None:
        try:
            profile = int(login_info.get("UserProfile", 0))
        except (TypeError, ValueError) as exc:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus returned an invalid user profile."
            ) from exc
        # WebLCT returns its raw enumeration here (1=SYSTEM, 2=Station
        # Operator, 3=Read/Write, 4=Monitor), not the UI access bit mask.
        if profile not in {1, 2}:
            raise DriverError(
                "INSUFFICIENT_PRIVILEGES",
                "ALFOplus native backup requires a Station Operator or SYSTEM account.",
            )

    @staticmethod
    def _is_foreign_active_session(
        login_info: dict,
        *,
        username: str,
        expected_address: str,
    ) -> bool:
        try:
            status = int(login_info.get("Status", 0))
        except (TypeError, ValueError):
            return True
        if status != 2:
            return False
        active_user = str(login_info.get("UserName", "")).upper()
        active_address = str(login_info.get("UserIp", ""))
        return active_user != username.upper() or active_address != expected_address

    @staticmethod
    def _ftp_mode_oid(*, snmp_version: int, user_ip: str, username: str) -> str:
        if snmp_version == 2:
            return ".5.8.0"
        try:
            octets = [str(int(value)) for value in user_ip.split(".")]
        except ValueError as exc:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus returned an invalid WebLCT client address."
            ) from exc
        if len(octets) != 4:
            raise DriverError(
                "WEBLCT_INVALID_RESPONSE", "ALFOplus returned an invalid WebLCT client address."
            )
        username_suffix = ".".join(str(ord(character)) for character in username.upper())
        return f".5.2.1.8.{'.'.join(octets)}.{username_suffix}"

    @staticmethod
    def _failure_message(failure) -> str:
        messages = {
            3: "ALFOplus could not verify the native backup data.",
            4: "ALFOplus could not prepare the native backup data.",
            5: "ALFOplus could not connect to or download from the FTP receiver.",
            6: "ALFOplus could not upload the native backup to the FTP receiver.",
            7: "ALFOplus rejected the file operation.",
            8: "ALFOplus could not create the configuration backup copy.",
            9: "The ALFOplus native backup was aborted.",
        }
        try:
            return messages.get(
                int(failure),
                "ALFOplus interrupted the native backup operation.",
            )
        except (TypeError, ValueError):
            return "ALFOplus interrupted the native backup operation."

    @staticmethod
    def _validate(context: DriverContext, options: dict) -> None:
        if not context.address:
            raise DriverError("NO_ADDRESS", "The device has no usable management address.")
        if context.credentials is None or not context.credentials.password:
            raise DriverError("NO_CREDENTIALS", "ALFOplus WebLCT requires password credentials.")
        if context.receiver is None or context.receiver.credentials is None:
            raise DriverError(
                "NO_RECEIVER_PROFILE", "Configure an enabled legacy FTP receiver profile."
            )
        receiver = context.receiver
        if receiver.protocol != "ftp" or receiver.mode != "direct":
            raise DriverError(
                "INVALID_RECEIVER_PROFILE",
                "Legacy ALFOplus requires a direct FTP receiver profile.",
            )
        if not receiver.advertised_host or receiver.advertised_port != 21:
            raise DriverError(
                "INVALID_RECEIVER_PROFILE",
                "Legacy ALFOplus requires an advertised FTP receiver on port 21.",
            )
        allowed = {
            "allow_device_export",
            "allow_legacy_ftp_setup",
            "sync_receiver_credentials",
            "web_port",
        }
        if set(options) - allowed:
            raise DriverError(
                "INVALID_DRIVER_OPTIONS", "Unsupported legacy ALFOplus driver option."
            )
        if options.get("allow_device_export") is not True:
            raise DriverError(
                "EXPORT_NOT_CONFIRMED",
                "Enable native device export before running an ALFOplus backup.",
            )
        if options.get("sync_receiver_credentials") is True:
            credentials = receiver.credentials
            if (
                len(credentials.username) > 8
                or not credentials.password
                or len(credentials.password) > 8
            ):
                raise DriverError(
                    "INVALID_RECEIVER_CREDENTIALS",
                    "Legacy ALFOplus FTP credentials must be at most 8 characters.",
                )
        web_port = options.get("web_port", 80)
        if (
            isinstance(web_port, bool)
            or not isinstance(web_port, int)
            or not 1 <= web_port <= 65535
        ):
            raise DriverError("INVALID_DRIVER_OPTIONS", "web_port must be 1-65535.")
