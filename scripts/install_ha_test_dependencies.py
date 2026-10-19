"""Install the frontend version needed by the Home Assistant runtime tests."""

import json
from pathlib import Path
import subprocess
import sys

import homeassistant


# Installing Core alone does not install its frontend. Read Core's own manifest
# so minimum-version and latest-version tests each get their matching frontend.
manifest = Path(homeassistant.__file__).parent / "components" / "frontend" / "manifest.json"
requirements = json.loads(manifest.read_text(encoding="utf-8"))["requirements"]
subprocess.run([sys.executable, "-m", "pip", "install", *requirements], check=True)
