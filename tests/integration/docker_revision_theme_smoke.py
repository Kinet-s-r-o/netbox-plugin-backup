"""Render real viewer templates with synthetic data, without database or storage writes.

Set NCB_THEME_PREVIEW_DIR to optionally export browser fixtures for visual QA.
The generated pages load the development NetBox's public CSS at localhost:8000.
"""

import os
from pathlib import Path
from types import SimpleNamespace

from django.template import Context, Engine

import netbox_config_backup

template_root = Path(netbox_config_backup.__file__).parent / "templates"
engine = Engine(
    dirs=[str(template_root)],
    loaders=[
        (
            "django.template.loaders.locmem.Loader",
            {
                "generic/object.html": """<!doctype html>
<html lang="en" data-bs-theme="{{ theme }}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Revision viewer - {{ theme }}</title>
<link rel="stylesheet" href="http://localhost:8000/static/netbox-external.css">
<link rel="stylesheet" href="http://localhost:8000/static/netbox.css">
</head><body><main class="container-xl py-4">{% block content %}{% endblock %}
<p class="mt-3">Ordinary inline code: <code id="outside-code">example</code></p>
</main></body></html>""",
            },
        ),
        "django.template.loaders.filesystem.Loader",
    ],
)
revision = SimpleNamespace(pk=1, revision_uuid="example-after", get_absolute_url=lambda: "#")
base = SimpleNamespace(pk=2, revision_uuid="example-before", created="Example revision")
lines = (
    "! Software Version EXAMPLE",
    "#",
    "sysname example-switch",
    "  description Preserved indentation",
    "",
    "password <redacted>",
    "note <script>alert('escaped')</script>",
    "description " + "long configuration line " * 20,
)
context = {
    "object": revision,
    "content": {
        "artifact": {"artifact_type": "running_config", "format": "network_config"},
        "lines": [{"number": i, "text": text} for i, text in enumerate(lines, 1)],
        "size": 1024,
        "truncated": False,
    },
    "downloadable_artifacts": [],
    "base_revision": base,
    "comparison_candidates": [base],
    "display_diff": {
        "lines": [
            {"kind": "file", "text": "--- example-before"},
            {"kind": "file", "text": "+++ example-after"},
            {"kind": "hunk", "text": "@@ -1,2 +1,2 @@"},
            {"kind": "context", "text": " ! Software Version EXAMPLE"},
            {"kind": "removed", "text": "-sysname old-switch"},
            {"kind": "added", "text": "+sysname example-switch"},
            {"kind": "context", "text": " password <redacted>"},
            {"kind": "context", "text": " note <script>alert('escaped')</script>"},
            {"kind": "added", "text": "+" + lines[-1]},
        ],
        "truncated": False,
    },
}
output_dir = os.environ.get("NCB_THEME_PREVIEW_DIR")
for theme in ("light", "dark"):
    for view in ("content", "diff"):
        template = engine.get_template(f"netbox_config_backup/configrevision_{view}.html")
        html = template.render(Context({**context, "theme": theme}))
        assert "--tblr-bg-surface-dark" not in html
        assert "background: var(--tblr-bg-surface, var(--tblr-body-bg))" in html
        assert ".ncb-code-line > code" in html and ".ncb-diff-line > code" in html
        assert "&lt;redacted&gt;" in html
        assert "&lt;script&gt;" in html and "<script>" not in html
        if view == "content":
            assert html.count('class="ncb-line-number"') == len(lines)
            assert "  description Preserved indentation" in html
        if output_dir:
            path = Path(output_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / f"{view}-{theme}.html").write_text(html, encoding="utf-8")

print({"revision_theme_templates": "passed", "rendered_pages": 4, "synthetic_data_only": True})
