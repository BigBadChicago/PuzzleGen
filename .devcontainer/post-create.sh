#!/usr/bin/env bash
# Everything a working environment needs, so a fresh Codespace can run the
# whole suite and rebuild a snapshot without a local toolchain.
#
# Python 3.13 rather than 3.11: the package requires 3.11 or later (StrEnum)
# and CI tests both, so the development image takes the newer one and lets CI
# catch a 3.11 incompatibility.
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
# The platform, with test and snapshot dependencies. The snapshot extra pulls
# wn, wordfreq, sentence-transformers and numpy; only the exporters import
# them, and only a published snapshot needs the model.
python -m pip install -e ".[dev,snapshot]"

# Every game in the repository. Globbed rather than listed: adding a game
# needs no edit here.
for game in games/*/; do
  [ -f "$game/pyproject.toml" ] || continue
  python -m pip install -e "$game"
done

echo
echo "Installed. Useful commands:"
echo "  scripts/test-all.sh                       every suite, platform first"
echo "  python -m pytest tests                    the platform alone"
echo "  bash docs/rebuild-with-batch2.sh          rebuild build A2 and measure it"
echo
echo "WordNet data for tools/export_wordnet.py is not installed; it is only"
echo "needed to export a new root. Fetch the 2024 edition from the"
echo "globalwordnet/english-wordnet GitHub release and add it with"
echo "python -c 'import wn; wn.add(\"english-wordnet-2024.xml.gz\")'"
