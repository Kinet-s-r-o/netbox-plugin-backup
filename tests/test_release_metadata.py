from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from netbox_config_backup.__about__ import __version__


class ReleaseMetadataTests(unittest.TestCase):
    def test_package_and_release_documents_use_one_version(self):
        project_root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = metadata["project"]["version"]

        self.assertEqual(project_version, __version__)
        self.assertIn(
            f"## [{project_version}]",
            (project_root / "CHANGELOG.md").read_text(encoding="utf-8"),
        )
        self.assertIn(
            f"| {project_version.rsplit('.', 1)[0]}.x |",
            (project_root / "COMPATIBILITY.md").read_text(encoding="utf-8"),
        )

    def test_release_image_installs_a_built_wheel(self):
        project_root = Path(__file__).resolve().parents[1]
        dockerfile = (project_root / "docker" / "Dockerfile.release").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv build --wheel", dockerfile)
        self.assertIn("/tmp/netbox-config-backup-dist/*.whl", dockerfile)
        self.assertNotIn("--editable", dockerfile)

    def test_receiver_overlay_uses_current_image_for_every_netbox_process(self):
        project_root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = metadata["project"]["version"]
        compose = (project_root / "docker" / "docker-compose.receiver.yml").read_text(
            encoding="utf-8"
        )
        expected_image = f"image: ${{NETBOX_IMAGE:-netbox-config-backup:{project_version}}}"
        services = (
            "netbox",
            "netbox-worker",
            "netbox-housekeeping",
            "config-backup-worker",
            "config-backup-receiver",
            "config-backup-legacy-ftp-receiver",
        )

        self.assertEqual(compose.count(expected_image), len(services))
        for service in services:
            self.assertIn(f"  {service}:\n", compose)

        housekeeping = compose.split("  netbox-housekeeping:\n", 1)[1].split(
            "\n  config-backup-worker:", 1
        )[0]
        self.assertIn("netbox-config-backup-data:/var/lib/netbox-config-backup", housekeeping)

        nas_compose = (project_root / "docker" / "docker-compose.nas-backup.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"image: netbox-config-backup-nas:{project_version}", nas_compose)

    def test_ci_exercises_fresh_install_and_data_preserving_upgrade(self):
        project_root = Path(__file__).resolve().parents[1]
        workflow = (project_root / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )

        for required in (
            "docker_upgrade_seed_0025.py",
            "docker_upgrade_assert_current.py",
            "collectstatic --noinput",
            "docker_smoke.py",
            "docker_device_backup_tab_smoke.py",
            "docker_storage_revision_inventory_smoke.py",
            "docker_run_cancellation_smoke.py",
            "docker_health_dashboard_smoke.py",
            "docker_target_delete_smoke.py",
            "docker_target_bulk_delete_smoke.py",
            "docker_revision_delete_smoke.py",
        ):
            self.assertIn(required, workflow)

    def test_overview_has_a_non_root_url(self):
        project_root = Path(__file__).resolve().parents[1]
        urls = (project_root / "netbox_config_backup" / "urls.py").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'path("overview/", views.ConfigBackupHomeView.as_view(), name="home")',
            urls,
        )
        self.assertIn('name="root"', urls)


if __name__ == "__main__":
    unittest.main()
