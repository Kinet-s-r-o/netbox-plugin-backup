import unittest
from contextlib import contextmanager

from netmiko.ssh_dispatcher import CLASS_MAPPER

from netbox_config_backup.credentials.base import CredentialMaterial
from netbox_config_backup.drivers import driver_registry
from netbox_config_backup.drivers.base import CollectedArtifact, DriverContext
from netbox_config_backup.drivers.network_vendors import (
    HuaweiVRPDriver,
    HuaweiVRPV8Driver,
)
from netbox_config_backup.transports import LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS
from netbox_config_backup.transports.netmiko import SSH_DISABLED_ALGORITHMS

PROFILE_CASES = (
    (
        "dell_os6",
        "dell_os6",
        "show running-config",
        "Current Configuration : 100 bytes\nhostname dell-os6\ninterface vlan 1\n",
    ),
    (
        "dell_os9",
        "dell_os9",
        "show running-config",
        "Current Configuration : 100 bytes\nhostname dell-os9\ninterface vlan 1\n",
    ),
    (
        "dell_os10",
        "dell_os10",
        "show running-configuration",
        "! Version 10.5.5\nhostname dell-os10\ninterface ethernet1/1/1\n",
    ),
    (
        "dell_powerconnect",
        "dell_powerconnect",
        "show running-config",
        "Current Configuration : 100 bytes\nhostname powerconnect\ninterface vlan 1\n",
    ),
    (
        "fiberstore_fsos",
        "fiberstore_fsos",
        "show running-config",
        "!Device running configuration:\n!version V310R230\nhostname fsos\n",
    ),
    (
        "fiberstore_fsosv2",
        "fiberstore_fsosv2",
        "show running-config",
        "!Device running configuration:\n!version V310R230\nhostname fsos-v2\n",
    ),
    (
        "hp_comware",
        "hp_comware",
        "display current-configuration",
        "version 7.1\nsysname comware\n#\ninterface Vlan-interface1\n",
    ),
    (
        "hp_procurve",
        "hp_procurve",
        "show running-config",
        "; J9773A Configuration Editor; Created on release #YA.16\nhostname procurve\n",
    ),
    (
        "huawei_vrp",
        "huawei_vrp",
        "display current-configuration",
        "!Software Version V200R022\nsysname huawei-vrp\n#\ninterface Vlanif1\n",
    ),
    (
        "huawei_vrpv8",
        "huawei_vrpv8",
        "display current-configuration",
        "!Software Version V800R022\nsysname huawei-v8\n#\ninterface Vlanif1\n",
    ),
    (
        "tplink_jetstream",
        "tplink_jetstream",
        "show running-config",
        "hostname tplink\ninterface gigabitEthernet 1/0/1\n",
    ),
    (
        "ubiquiti_edgerouter",
        "ubiquiti_edgerouter",
        "show configuration commands",
        "set system host-name edge-router\nset interfaces ethernet eth0 address dhcp\n",
    ),
    (
        "ubiquiti_edgeswitch",
        "ubiquiti_edgeswitch",
        "show running-config",
        "!Current Configuration:\nhostname edge-switch\ninterface 0/1\n",
    ),
    (
        "zte_zxros",
        "zte_zxros",
        "show running-config",
        "hostname zte-router\ninterface gei-0/1\nversion V4.6\n",
    ),
)


class RecordingSession:
    def __init__(self, output):
        self.output = output
        self.commands = []

    def send_command(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return self.output


class RecordingTransport:
    def __init__(self, output):
        self.session = RecordingSession(output)
        self.opens = []

    @contextmanager
    def open(self, *, device_type, context):
        self.opens.append((device_type, context))
        yield self.session


def make_context():
    return DriverContext(
        device_id=1,
        device_name="switch-1",
        address="192.0.2.10",
        credentials=CredentialMaterial(username="backup", password="secret"),
    )


class NetworkVendorDriverTests(unittest.TestCase):
    def test_legacy_rsa_host_key_exception_is_scoped_to_vrp(self):
        vrp = HuaweiVRPDriver()
        vrpv8 = HuaweiVRPV8Driver()

        self.assertEqual(
            vrp.transport._disabled_algorithms,
            LEGACY_RSA_HOST_KEY_DISABLED_ALGORITHMS,
        )
        self.assertNotIn("keys", vrp.transport._disabled_algorithms)
        self.assertEqual(vrpv8.transport._disabled_algorithms, SSH_DISABLED_ALGORITHMS)

    def test_profiles_use_installed_netmiko_platforms_and_collect_safely(self):
        for driver_id, device_type, command, output in PROFILE_CASES:
            with self.subTest(driver_id=driver_id):
                self.assertIn(device_type, CLASS_MAPPER)
                transport = RecordingTransport(output)
                driver_class = type(driver_registry.create(driver_id))
                driver = driver_class(transport)
                context = make_context()

                artifact = driver.collect(context)[0]

                self.assertEqual(transport.opens, [(device_type, context)])
                self.assertEqual(transport.session.commands[0][0], command)
                self.assertTrue(driver.validate(artifact).valid)
                self.assertTrue(artifact.is_primary)

    def test_profiles_reject_command_errors_and_incomplete_output(self):
        for driver_id, _device_type, _command, _output in PROFILE_CASES:
            driver = driver_registry.create(driver_id)
            with self.subTest(driver_id=driver_id):
                rejected = driver.validate(
                    CollectedArtifact(
                        artifact_type="running_config",
                        filename="running-config.cfg",
                        content=b"% Invalid input detected at '^' marker.\n",
                        is_primary=True,
                    )
                )
                incomplete = driver.validate(
                    CollectedArtifact(
                        artifact_type="running_config",
                        filename="running-config.cfg",
                        content=b"configuration output without an identity marker\n",
                        is_primary=True,
                    )
                )
                self.assertEqual(rejected.error_code, "COMMAND_REJECTED")
                self.assertEqual(incomplete.error_code, "INCOMPLETE_CONFIG")


if __name__ == "__main__":
    unittest.main()
