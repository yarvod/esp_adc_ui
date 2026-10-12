"""Only validated version tags can become build labels or release filenames."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.updates import version_key
from application.version import __version__

version = os.environ["REF_NAME"] if os.environ.get("REF_TYPE") == "tag" else __version__
version_key(version)
print(version)
