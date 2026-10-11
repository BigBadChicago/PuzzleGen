"""This distribution's own test setup.

Short on purpose. The graph fixtures and record builders come from
``puzzlegen.testing``, a pytest plugin the platform registers, so nothing here
reaches into the platform's test directory and nothing is copied. What remains
is this game's own: its tests directory on the path, so the helpers beside this
file import by name.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
