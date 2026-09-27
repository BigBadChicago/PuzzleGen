# PuzzleGen

A daily puzzle game engine over a shared, governed knowledge graph. One
governed body of facts, many independent game lenses, one engine between
them.

Start here:

- `docs/architecture.md` — full decision record, every phase.
- `docs/handoff.md` — current status and what to do next.

## Development

```
pip install -e ".[dev]"
pytest
```

Snapshot-building tools (`tools/export_*.py`) require the extra dependencies
in `pip install -e ".[snapshot]"`. The engine itself never imports them.
