"""Where runtime state lives — one answer, so it can be moved in one place.

Three modules keep state on disk, and all three used to derive their own path
from `__file__`. That works right up until the code runs somewhere the package
directory is read-only, which is exactly what a Lambda deployment is. Rather
than teach each module about that, the location is decided here and overridable
by environment.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "data"

DATA_DIR = Path(os.environ.get("AFH_DATA_DIR", _DEFAULT))
SNAPSHOT_DIR = DATA_DIR / "snapshots"
HEALTH_FILE = DATA_DIR / "source_health.json"
ACK_FILE = DATA_DIR / "acknowledged.json"
PROFILE_FILE = DATA_DIR / "profile.json"
