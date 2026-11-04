"""Packaging checks for the self-contained HACS integration."""

import json
from pathlib import Path


def test_manifest_has_no_external_python_requirements() -> None:
    """HACS installation must not fetch the standalone client from PyPI."""
    manifest_path = Path("custom_components/ok_home/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "requirements" not in manifest


def test_integration_has_vendored_api_modules() -> None:
    """All runtime client modules must be present inside the integration directory."""
    api_path = Path("custom_components/ok_home/api")
    assert {path.name for path in api_path.glob("*.py")} >= {
        "__init__.py",
        "auth.py",
        "client.py",
        "exceptions.py",
        "models.py",
    }
