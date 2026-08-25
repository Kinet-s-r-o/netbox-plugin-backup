import io
import unittest
import warnings
import zipfile

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import DriverContext, DriverError, ReceiverParameters
from netbox_config_backup.drivers.native_exports import CERAGON_SIAE_DRIVERS
from netbox_config_backup.transports.ssh_artifact import SshArtifactResult


class RecordingTransport:
    def __init__(self, content=b"native vendor backup"):
        self.content = content
        self.calls = []

    def collect(self, context, **kwargs):
        self.calls.append((context, kwargs))
        return SshArtifactResult(content=self.content)


class RecordingLegacyTransport:
    def __init__(self, content=b"legacy alfoplus backup"):
        self.content = content
        self.calls = []

    def collect(self, context, **kwargs):
        self.calls.append((context, kwargs))
        return self.content


def make_context(options):
    return DriverContext(
        device_id=1,
        device_name="microwave-1",
        address="192.0.2.80",
        credentials=CredentialMaterial(username="backup", password="secret"),
        options=options,
    )


def make_legacy_context(options):
    return DriverContext(
        device_id=1,
        device_name="alfoplus-1",
        address="192.0.2.80",
        credentials=CredentialMaterial(username="SYSTEM", password="SIAEMICR"),
        receiver=ReceiverParameters(
            profile_id=2,
            protocol="ftp",
            mode="direct",
            advertised_host="192.0.2.10",
            advertised_port=21,
            bridge_host="config-backup-receiver",
            bridge_port=21,
            remote_bind_host="127.0.0.1",
            remote_bind_port=2222,
            upload_directory="incoming",
            inbox_path="/tmp/incoming",
            credentials=CredentialMaterial(username="NCBFTP", password="BACKUP1"),
        ),
        options=options,
    )


def valid_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "config_dump.txt",
            "header_ver=1\n"
            "creation_date_time =2026-08-18 10:00\n"
            "system-name=microwave-1\n"
            "snmp-community=private-value\n"
            "configuration-table-file-transfer-log-status.0.ID=1\n"
            "configuration-table-file-transfer-log-status\n"
            "1|2026-08-18|upload complete\n"
            "%%%\n",
        )
        archive.writestr("backup.tar.gz", b"opaque restore data")
    return output.getvalue()


class NativeExportDriverTests(unittest.TestCase):
    def test_all_model_profiles_are_registered(self):
        expected = {
            "ceragon_ip20",
            "ceragon_ip50",
            "siae_alfoplus",
            "siae_alfoplus2",
            "siae_alfoplus80hd",
            "siae_ags20",
        }
        self.assertTrue(expected.issubset(set(driver_registry.ids())))
        self.assertEqual({item.driver_id for item in CERAGON_SIAE_DRIVERS}, expected)

    def test_driver_requires_explicit_remote_path(self):
        transport = RecordingTransport()
        driver_class = type(driver_registry.create("siae_alfoplus2"))
        with self.assertRaises(DriverError) as raised:
            driver_class(transport).collect(make_context({}))
        self.assertEqual(raised.exception.error_code, "DRIVER_SETUP_REQUIRED")
        self.assertEqual(transport.calls, [])

    def test_export_command_requires_explicit_confirmation(self):
        driver_class = type(driver_registry.create("siae_alfoplus2"))
        with self.assertRaises(DriverError) as raised:
            driver_class(RecordingTransport()).collect(
                make_context({"remote_path": "backup.bku", "export_command": "vendor backup"})
            )
        self.assertEqual(raised.exception.error_code, "EXPORT_COMMAND_NOT_CONFIRMED")

    def test_legacy_alfoplus_uses_weblct_transport(self):
        transport = RecordingLegacyTransport()
        driver_class = type(driver_registry.create("siae_alfoplus"))
        driver = driver_class(transport)
        artifacts = driver.collect(
            make_legacy_context(
                {
                    "allow_device_export": True,
                    "allow_legacy_ftp_setup": True,
                    "sync_receiver_credentials": True,
                }
            )
        )

        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(
            transport.calls[0][1]["options"]["allow_device_export"],
            True,
        )
        self.assertEqual(
            {item.artifact_type for item in artifacts}, {"backup_manifest", "native_backup"}
        )
        self.assertTrue(all(driver.validate(item).valid for item in artifacts))

    def test_siae_collects_manifest_and_opaque_native_file(self):
        transport = RecordingTransport()
        driver_class = type(driver_registry.create("siae_alfoplus2"))
        driver = driver_class(transport)
        artifacts = driver.collect(
            make_context(
                {
                    "remote_path": "exports/config.bku",
                    "export_command": "backup export config.bku",
                    "allow_export_command": True,
                }
            )
        )

        self.assertEqual(transport.calls[0][1]["export_command"], "backup export config.bku")
        self.assertTrue(artifacts[0].is_primary)
        self.assertTrue(all(driver.validate(item).valid for item in artifacts))

    def test_ceragon_rejects_unsafe_or_corrupt_zip(self):
        driver_class = type(driver_registry.create("ceragon_ip50"))
        driver = driver_class(RecordingTransport(b"not a zip"))
        with self.assertRaises(DriverError) as raised:
            driver.collect(make_context({"remote_path": "backup.zip"}))
        self.assertEqual(raised.exception.error_code, "INVALID_ARCHIVE")

        transport = RecordingTransport(valid_zip())
        driver = driver_class(transport)
        artifacts = driver.collect(make_context({"remote_path": "backup.zip"}))
        by_type = {item.artifact_type: item for item in artifacts}
        self.assertEqual(
            set(by_type),
            {"configuration_dump", "backup_manifest", "native_backup"},
        )
        self.assertTrue(by_type["configuration_dump"].is_primary)
        self.assertFalse(by_type["backup_manifest"].is_primary)
        self.assertTrue(all(driver.validate(item).valid for item in artifacts))

        duplicate = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(duplicate, "w") as archive:
                archive.writestr("config_dump.txt", "header_ver=1\n")
                archive.writestr("config_dump.txt", "header_ver=2\n")
        with self.assertRaises(DriverError) as raised:
            driver_class(RecordingTransport(duplicate.getvalue())).collect(
                make_context({"remote_path": "backup.zip"})
            )
        self.assertEqual(raised.exception.error_code, "INVALID_ARCHIVE")

        unsafe = io.BytesIO()
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("config_dump.txt", "header_ver=1\n")
            archive.writestr("../escape.txt", "unsafe")
        with self.assertRaises(DriverError) as raised:
            driver_class(RecordingTransport(unsafe.getvalue())).collect(
                make_context({"remote_path": "backup.zip"})
            )
        self.assertEqual(raised.exception.error_code, "INVALID_ARCHIVE")

    def test_ceragon_normalizes_export_runtime_state_and_redacts_secrets(self):
        driver_class = type(driver_registry.create("ceragon_ip50"))
        driver = driver_class(RecordingTransport(valid_zip()))
        primary = next(
            item
            for item in driver.collect(make_context({"remote_path": "backup.zip"}))
            if item.is_primary
        )

        normalized = driver.normalize(primary).decode()
        self.assertIn("system-name=microwave-1", normalized)
        self.assertNotIn("creation_date_time", normalized)
        self.assertNotIn("file-transfer-log-status", normalized)

        rendered = driver.redact_for_display(primary.content.decode())
        self.assertIn("snmp-community=<redacted>", rendered)
        self.assertNotIn("private-value", rendered)


if __name__ == "__main__":
    unittest.main()
