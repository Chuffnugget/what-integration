"""Build the release asset extracted directly into custom_components/what."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import argparse
import json

def package(destination, tag=None):
    root = Path(__file__).resolve().parents[1]
    source = root / "custom_components/what"
    version = json.loads((source / "manifest.json").read_text(encoding="utf-8"))["version"]
    if tag and tag.removeprefix("v") != version:
        raise ValueError(f"Release tag {tag} does not match manifest version {version}")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                archive.write(path, path.relative_to(source).as_posix())
        for name in ("LICENSE", "COPYING.GPL-3.0", "COPYRIGHT"):
            archive.write(root / name, name)
    with ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert "manifest.json" in archive.namelist()
        assert "__init__.py" in archive.namelist()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--tag")
    args = parser.parse_args()
    package(args.destination, args.tag)
