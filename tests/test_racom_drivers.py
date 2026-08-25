import base64
import io
import json
import tarfile
import unittest
import zipfile

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import ConnectionParameters, DriverContext, DriverError
from netbox_config_backup.drivers.racom import (
    RacomRAy2Driver,
    RacomRAy3Driver,
    RacomRipEX2Driver,
)
from netbox_config_backup.transports.ssh_artifact import (
    RacomRaySshArtifactTransport,
    SshArtifactResult,
)


def zip_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("configuration/config.json", '{"station":"ripEX2"}')
    return output.getvalue()


def tgz_bytes():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for unit in ("L", "U"):
            # RAy native files omit the final outer-object brace.
            content = (
                b'{"system":{"UNIT":"'
                + unit.encode()
                + b'","SNMP_COMMUNITY_STRING":"private-value"}}'[:-1]
            )
            info = tarfile.TarInfo(f"{unit}.conf")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def legacy_tgz_bytes():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for unit in ("L", "U"):
            content = (
                f"# Legacy RAy configuration\nUNIT={unit}\n"
                "SVC_STATION_NAME=radio\nSNMP_COMMUNITY_STRING=private-value\n"
            ).encode()
            info = tarfile.TarInfo(f"{unit}.conf")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


def context(*, port=443, options=None):
    return DriverContext(
        device_id=7,
        device_name="radio-7",
        address="192.0.2.70",
        credentials=CredentialMaterial(username="backup", password="secret"),
        connection=ConnectionParameters(port=port),
        options=options or {},
    )


class RecordingHttpTransport:
    def __init__(self):
        self.calls = []

    def post_json(self, url, payload, **kwargs):
        self.calls.append((url, payload, kwargs))
        if url.endswith("login.cgi"):
            return {"token": "session-token"}
        if url.endswith("logout.cgi"):
            return {}
        if payload["method"] == "settings_get":
            return {
                "result": {
                    "config_tree": {},
                    "config_meta": {},
                    "config_data": {"main": {"RR_StationDesc": "Radio 7"}},
                }
            }
        return {"result": {"base64": base64.b64encode(zip_bytes()).decode()}}


class RecordingSftpTransport:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def collect(self, target_context, **kwargs):
        self.calls.append((target_context, kwargs))
        return SshArtifactResult(content=self.content, command_output="backup completed")


class RacomDriverTests(unittest.TestCase):
    def test_all_racom_drivers_are_registered(self):
        self.assertIsInstance(driver_registry.create("racom_ripex2"), RacomRipEX2Driver)
        self.assertIsInstance(driver_registry.create("racom_ray2"), RacomRAy2Driver)
        self.assertIsInstance(driver_registry.create("racom_ray3"), RacomRAy3Driver)

    def test_ray_drivers_use_vendor_scoped_ssh_compatibility(self):
        for driver_class in (RacomRAy2Driver, RacomRAy3Driver):
            driver = driver_class()

            with self.subTest(driver=driver.driver_id):
                self.assertIsInstance(driver.transport, RacomRaySshArtifactTransport)

    def test_ripex2_collects_structured_and_native_backup_and_logs_out(self):
        transport = RecordingHttpTransport()
        driver = RacomRipEX2Driver(transport)

        artifacts = driver.collect(context(options={"verify_tls": False}))

        self.assertEqual(
            [item.artifact_type for item in artifacts], ["structured_config", "native_backup"]
        )
        self.assertTrue(artifacts[0].is_primary)
        self.assertFalse(artifacts[1].is_primary)
        self.assertTrue(all(driver.validate(item).valid for item in artifacts))
        configuration = json.loads(artifacts[0].content)
        self.assertEqual(configuration["config_data"]["main"]["RR_StationDesc"], "Radio 7")
        self.assertTrue(transport.calls[-1][0].endswith("logout.cgi"))
        self.assertEqual(transport.calls[1][2]["headers"], {"apikey": "session-token"})

    def test_ripex2_redacts_nested_secret_values(self):
        rendered = RacomRipEX2Driver(RecordingHttpTransport()).redact_for_display(
            '{"password":"one","nested":{"community":"two","name":"safe"}}'
        )
        self.assertNotIn("one", rendered)
        self.assertNotIn("two", rendered)
        self.assertIn("safe", rendered)

    def test_ray_drivers_run_fixed_command_and_collect_valid_tgz(self):
        for driver_class in (RacomRAy2Driver, RacomRAy3Driver):
            transport = RecordingSftpTransport(tgz_bytes())
            driver = driver_class(transport)

            artifacts = driver.collect(context(port=22))

            with self.subTest(driver=driver.driver_id):
                self.assertEqual(
                    transport.calls[0][1]["export_command"],
                    ". /etc/profile >/dev/null 2>&1; cli_cnf_backup_get",
                )
                self.assertEqual(transport.calls[0][1]["remote_path"], "cnf_backup.tgz")
                self.assertTrue(all(driver.validate(item).valid for item in artifacts))
                self.assertTrue(artifacts[0].is_primary)
                self.assertEqual(
                    [item.artifact_type for item in artifacts],
                    ["configuration_dump", "backup_manifest", "native_backup"],
                )
                self.assertEqual(artifacts[2].content, tgz_bytes())
                rendered = driver.redact_for_display(artifacts[0].content.decode())
                self.assertIn('"L"', rendered)
                self.assertIn('"U"', rendered)
                self.assertIn("<redacted>", rendered)
                self.assertNotIn("private-value", rendered)

    def test_ray_driver_parses_and_redacts_legacy_assignment_backup(self):
        driver = RacomRAy3Driver(RecordingSftpTransport(legacy_tgz_bytes()))

        artifacts = driver.collect(context(port=22))

        configuration = json.loads(artifacts[0].content)
        self.assertEqual(configuration["L"]["SVC_STATION_NAME"], "radio")
        rendered = driver.redact_for_display(artifacts[0].content.decode())
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("private-value", rendered)

    def test_ripex2_rejects_unknown_options_before_network_call(self):
        transport = RecordingHttpTransport()
        with self.assertRaises(DriverError) as raised:
            RacomRipEX2Driver(transport).collect(context(options={"rpc_method": "reboot_init"}))
        self.assertEqual(raised.exception.error_code, "INVALID_DRIVER_OPTIONS")
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
