"""Validate the exact archive layout HACS extracts into its integration folder."""
import importlib.util
import json
from pathlib import Path
from zipfile import ZipFile
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_hacs", ROOT / "scripts/package_hacs.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)

def test_release_asset_layout_and_manifest(tmp_path):
    config = json.loads((ROOT / "hacs.json").read_text())
    assert config["zip_release"] is True
    assert config["filename"] == "what.zip"
    output = tmp_path / config["filename"]
    version = json.loads((ROOT / "custom_components/what/manifest.json").read_text())["version"]
    packager.package(output, "v" + version)
    with ZipFile(output) as archive:
        names = archive.namelist()
        assert "manifest.json" in names and "__init__.py" in names
        assert not any(
            (name.startswith("custom_components/") or "__pycache__" in name or name.endswith(".pyc") for name in names),
        )
        assert json.loads(archive.read("manifest.json"))["version"] == version

def test_release_tag_must_match_manifest(tmp_path):
    with pytest.raises(ValueError):
        packager.package(tmp_path / "what.zip", "v0.0.0-wrong")
