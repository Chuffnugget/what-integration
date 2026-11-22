"""Install Home Assistant component requirements used by runtime tests."""

import json
from pathlib import Path
import subprocess
import sys

import homeassistant


# Importing the camera API for snapshot tests also imports the stream component.
# Use the pins from the Core version installed in this matrix job.
components = Path(homeassistant.__file__).parent / "components"
requirements = set()
for domain in ("frontend", "camera", "stream"):
    manifest = components / domain / "manifest.json"
    requirements.update(json.loads(manifest.read_text(encoding="utf-8"))["requirements"])
subprocess.run([sys.executable, "-m", "pip", "install", *sorted(requirements)], check=True)
