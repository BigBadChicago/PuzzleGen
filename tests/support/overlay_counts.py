"""What the platform's overlay fixture holds, read from it and never typed.

The platform's tool tests used to assert against game one's committed overlay
seed, which coupled this suite to a game's content: the numbers moved whenever
a curator retired a category, and the platform suite could not run at all once
the game was uninstalled. They now read ``overlay-mini.curated.json``, a
fixture of four entities beside the eight synset ``wordnet-mini`` lexicon, for
the same reason that lexicon exists.

Game one's own assertions about its own overlay live with game one.
"""

from __future__ import annotations

import json
from pathlib import Path

SEED = (
    Path(__file__).resolve().parents[2]
    / "content"
    / "seeds"
    / "overlay-mini.curated.json"
)

_document = json.loads(SEED.read_text(encoding="utf-8"))

#: Live categories, the ones a build imports.
CATEGORIES = len(_document["categories"])
#: Distinct entities the fixture contributes.
ENTITIES = len(_document["entities"])
#: Entity to category memberships, which is the relationship count an import makes.
MEMBERSHIPS = sum(len(e["categories"]) for e in _document["entities"])
