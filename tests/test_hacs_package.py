"""Packaging checks for the self-contained HACS integration."""

import json
from pathlib import Path

from custom_components.ok_home.api.models import Address as VendoredAddress
from okhome.models import Address


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


def test_street_address_matches_app_naming_in_both_clients() -> None:
    """Canonical and vendored clients expose the same short location name."""
    payload = {
        "addressStreetName": "Testvej",
        "addressHouseNumber": "12A",
        "addressPostalCode": 8000,
        "addressPostalOfficeName": "Aarhus C",
        "addressFloor": "2",
        "addressDoor": "th.",
        "netArea": "TEST",
    }
    canonical = Address.from_dict(payload)
    vendored = VendoredAddress.from_dict(payload)

    assert canonical.street_address == "Testvej 12A"
    assert vendored.street_address == canonical.street_address
    assert canonical.formatted == "Testvej 12A, 2 th., 8000 Aarhus C"
