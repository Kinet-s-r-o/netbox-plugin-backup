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
        ):
            self.assertIn(required, workflow)


if __name__ == "__main__":
    unittest.main()
