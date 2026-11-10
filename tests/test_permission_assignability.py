import ast
import unittest
from pathlib import Path

MODELS = Path(__file__).resolve().parents[1] / "netbox_config_backup" / "models.py"


def _private_models() -> set[str]:
    """Names of model classes which set ``_netbox_private = True`` (parsed, not imported)."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    private = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "_netbox_private"
                    for target in statement.targets
                )
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is True
            ):
                private.add(node.name)
    return private


class PermissionAssignabilityTests(unittest.TestCase):
    def test_config_artifact_stays_assignable_in_the_object_permissions_form(self):
        # NetBox hides private models from that form. Viewing and downloading content
        # require view_configartifact, so hiding it would make those rights impossible
        # to grant from the UI (only superusers and command-created groups would work).
        self.assertNotIn("ConfigArtifact", _private_models())

    def test_secret_holding_models_remain_private(self):
        self.assertTrue({"StoredCredential", "DownloadEncryptionSecret"} <= _private_models())


if __name__ == "__main__":
    unittest.main()
