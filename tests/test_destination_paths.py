from uuid import UUID

from netbox_config_backup.services.destination_paths import (
    device_directory_name,
    revision_destination_path,
)


def test_device_directory_uses_readable_hostname():
    assert device_directory_name("router-01.example.sk", 187) == "router-01.example.sk"


def test_device_directory_sanitizes_display_names_and_traversal():
    assert device_directory_name("SIAE – Žilina / ../../ALFO+", 42) == "SIAE-Zilina-ALFO"


def test_device_directory_falls_back_to_stable_device_id():
    assert device_directory_name("../..", 42) == "device-42"


def test_revision_destination_path_is_absolute_and_hostname_based():
    revision_uuid = UUID("11111111-2222-3333-4444-555555555555")
    assert revision_destination_path(
        "/netbox-config-backup/",
        device_name="core-router-01",
        device_id=187,
        revision_uuid=revision_uuid,
    ) == (
        "/netbox-config-backup/devices/core-router-01/revisions/"
        "11111111-2222-3333-4444-555555555555"
    )
