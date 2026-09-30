"""What the committed overlay seed holds, read from it and never typed.

Tests used to state these as literals (15 categories, 144 entities, 150
memberships), which made every retirement or reopening a hunt through five test
files for numbers that had merely become stale. The seed is the source of
truth, so the tests that only need to agree with it read it.

``test_overlay_seed.py`` still pins the live category count on purpose: that is
the guard against a category disappearing by accident, and it should change
only when someone means it to.
"""

from __future__ import annotations

import json
from pathlib import Path

SEED = Path(__file__).resolve().parents[2] / "content" / "seeds" / "overlay.curated.json"

_document = json.loads(SEED.read_text(encoding="utf-8"))

#: Live categories, the ones a build imports.
CATEGORIES = len(_document["categories"])
#: Distinct entities the seed contributes.
ENTITIES = len(_document["entities"])
#: Entity to category memberships, which is the relationship count an import makes.
MEMBERSHIPS = sum(len(e["categories"]) for e in _document["entities"])
