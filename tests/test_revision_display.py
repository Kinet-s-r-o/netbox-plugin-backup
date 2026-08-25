import hashlib
import unittest

from netbox_config_backup.services.revision_display import (
    RevisionDisplayError,
    build_display_diff,
    prepare_display_content,
)


def prepare(content: bytes):
    return prepare_display_content(
        content,
        driver_id="mikrotik_routeros",
        expected_size=len(content),
        expected_hash=hashlib.sha256(content).hexdigest(),
        max_bytes=1024,
    )


class RevisionDisplayTests(unittest.TestCase):
    def test_content_is_integrity_checked_decoded_and_redacted(self):
        raw = (
            b"/system identity set name=router-1\n/ppp secret add name=user password=very-secret\n"
        )

        rendered = prepare(raw)

        self.assertEqual(rendered.size, len(raw))
        self.assertEqual(len(rendered.lines), 2)
        self.assertEqual(rendered.lines[0].number, 1)
        self.assertIn("<redacted>", rendered.text)
        self.assertNotIn("very-secret", rendered.text)

    def test_invalid_integrity_size_encoding_and_driver_fail_closed(self):
        raw = b"/system identity set name=router-1\n"
        cases = (
            {"expected_size": len(raw) + 1},
            {"expected_hash": "0" * 64},
            {"max_bytes": 1},
            {"driver_id": "missing"},
        )
        defaults = {
            "driver_id": "mikrotik_routeros",
            "expected_size": len(raw),
            "expected_hash": hashlib.sha256(raw).hexdigest(),
            "max_bytes": 1024,
        }
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(RevisionDisplayError):
                prepare_display_content(raw, **(defaults | overrides))

        with self.assertRaises(RevisionDisplayError):
            prepare_display_content(
                b"\xff",
                driver_id="mikrotik_routeros",
                expected_size=1,
                expected_hash=hashlib.sha256(b"\xff").hexdigest(),
                max_bytes=1024,
            )

    def test_diff_uses_prepared_redacted_content_and_classifies_lines(self):
        before = prepare(
            b"/system identity set name=old\n/ppp secret add name=user password=old-secret\n"
        )
        after = prepare(
            b"/system identity set name=new\n/ppp secret add name=user password=new-secret\n"
        )

        result = build_display_diff(
            before,
            after,
            before_label="before",
            after_label="after",
            max_lines=100,
        )

        self.assertFalse(result.truncated)
        self.assertTrue(any(line.kind == "added" for line in result.lines))
        self.assertTrue(any(line.kind == "removed" for line in result.lines))
        combined = "\n".join(line.text for line in result.lines)
        self.assertNotIn("old-secret", combined)
        self.assertNotIn("new-secret", combined)
        self.assertIn("<redacted>", combined)

    def test_diff_limit_is_enforced(self):
        before = prepare(b"/one\n/two\n")
        after = prepare(b"/three\n/four\n")

        result = build_display_diff(
            before,
            after,
            before_label="before",
            after_label="after",
            max_lines=2,
        )

        self.assertTrue(result.truncated)
        self.assertEqual(len(result.lines), 2)

    def test_large_content_can_be_integrity_checked_and_safely_truncated(self):
        raw = (
            b"/system identity set name=router-1\n"
            b"/ppp secret add name=user password=very-secret\n"
            b"/system note set note=tail\n"
        )

        rendered = prepare_display_content(
            raw,
            driver_id="mikrotik_routeros",
            expected_size=len(raw),
            expected_hash=hashlib.sha256(raw).hexdigest(),
            max_bytes=100,
            allow_truncate=True,
        )

        self.assertTrue(rendered.truncated)
        self.assertEqual(rendered.size, len(raw))
        self.assertLess(rendered.displayed_size, rendered.size)
        self.assertNotIn("very-secret", rendered.text)
        self.assertIn("<redacted>", rendered.text)


if __name__ == "__main__":
    unittest.main()
