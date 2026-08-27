from __future__ import annotations

import gettext
import re
import unittest
from pathlib import Path


class TemplateLocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.project_root = Path(__file__).resolve().parents[1]
        cls.template_root = (
            cls.project_root / "netbox_config_backup" / "templates" / "netbox_config_backup"
        )
        locale_root = cls.project_root / "netbox_config_backup" / "locale" / "sk" / "LC_MESSAGES"
        with (locale_root / "django.mo").open("rb") as message_file:
            cls.slovak_catalog = gettext.GNUTranslations(message_file)._catalog

    @staticmethod
    def _literal_messages(template: str) -> set[str]:
        translated = re.findall(r'''{%\s*translate\s+["'](.+?)["']\s*%}''', template)
        included = [
            match.group("value")
            for match in re.finditer(
                r'''\b(?:title|description)=(?P<quote>["'])(?P<value>.+?)(?P=quote)''',
                template,
            )
        ]
        return set(translated + included)

    def test_help_and_settings_use_single_translatable_templates(self):
        for name in ("help", "advanced_settings"):
            template = (self.template_root / f"{name}.html").read_text(encoding="utf-8")
            self.assertIn("i18n", template)
            self.assertFalse((self.template_root / f"{name}_sk.html").exists())

        views = (self.project_root / "netbox_config_backup" / "views.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("advanced_settings_sk.html", views)
        self.assertNotIn("help_sk.html", views)

    def test_slovak_catalog_covers_all_help_and_settings_literals(self):
        messages: set[str] = set()
        for name in ("help.html", "advanced_settings.html"):
            template = (self.template_root / name).read_text(encoding="utf-8")
            messages.update(self._literal_messages(template))

        missing = sorted(message for message in messages if message not in self.slovak_catalog)
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
