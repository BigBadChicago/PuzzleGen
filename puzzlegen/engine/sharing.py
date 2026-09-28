"""Share artifacts: rendered by the engine, redacted before they leave it.

A share is pasted into a public timeline on the day the puzzle is live. That
makes it three things at once, and each of them is a way to get this wrong: it
must not spoil the puzzle for anybody reading it, it must not identify the
player who posted it, and it must be readable by somebody who cannot see the
glyphs. The redactor enforces the first two and the three-rendering rule
enforces the third.

The leak that phase 4 found by test rather than by inspection was a dependency
ledger whose lopsided shape revealed an answer. The same class of leak lives
here: a game is free to build any grid it likes from its own tokens, so the
engine checks the finished text rather than trusting the intent behind it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ..core import ids
from ..core.errors import BoundaryViolationError
from ..core.versions import SHARE_FORMAT_VERSION
from .plugin import GameDescriptor, GamePlugin, Puzzle, ShareArtifact, ShareTokenSpec
from .scoring import build_telemetry
from .sessions import SessionRecord

#: Hard caps. A share is meant to be pasted, so anything approaching the size
#: of the puzzle itself is a sign the game is exporting its board.
MAX_SHARE_CHARS = 1200
MAX_SHARE_LINES = 24
MAX_TOKENS_PER_ROW = 32

#: Punctuation and characters the engine itself writes into share text. Every
#: other non-token codepoint is refused.
_STRUCTURAL = set(" \n#:.,!?'()/-+")

#: Any record id, in any of the engine's kinds.
_RECORD_ID = re.compile(r"\b(?:[a-z]+):[a-z0-9][a-z0-9_.-]{0,95}\b")

#: A run of hex long enough to be a content hash or a fingerprint.
_HEX_RUN = re.compile(r"\b[0-9a-f]{12,}\b")

_WORD = re.compile(r"[a-z0-9]+")

#: Words too common to carry a spoiler, excluded so the check stays usable.
#: A puzzle prompt containing "which" must not make the word "which"
#: unsayable in engine-written text.
_STOPWORDS = frozenset(
    {
        "that",
        "this",
        "with",
        "from",
        "they",
        "them",
        "have",
        "does",
        "here",
        "when",
        "what",
        "which",
        "your",
        "into",
        "each",
        "been",
        "were",
        "will",
        "than",
        "then",
        "some",
        "more",
        "most",
        "only",
        "also",
        "other",
        "these",
        "those",
        "there",
        "about",
        "belong",
        "puzzle",
        "answer",
        "options",
        "option",
        "prompt",
        "group",
        "round",
    }
)


class ShareLeak(BoundaryViolationError):
    """A rendered share said something it is not allowed to say."""


@dataclass(frozen=True, slots=True)
class RenderedShare:
    """A share in its three renderings, already redacted.

    ``text`` is what gets pasted. ``text_plain`` is the same grid in ASCII,
    for a client that cannot render the glyphs and for a reader that would
    otherwise announce every glyph by its Unicode name. ``alt_text`` is a
    sentence, because a grid read cell by cell tells nobody what happened.
    """

    id: str
    game_id: str
    day_key: str
    outcome: str
    text: str
    text_plain: str
    alt_text: str
    share_format_version: int = SHARE_FORMAT_VERSION
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.text or not self.text_plain or not self.alt_text:
            raise ValueError(
                "a share needs all three renderings; accessibility that is "
                "optional is accessibility that is absent"
            )


def forbidden_terms(puzzle: Puzzle) -> frozenset[str]:
    """Words a share must not contain, taken from the puzzle itself.

    Every string anywhere in the payload or the solution, flattened. This is
    deliberately blunt: a share naming any word the puzzle is built from is
    either spoiling it or is about to, and a game that wants to name something
    can put it in its own headline only if the puzzle never used it.
    """
    terms: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            for word in _WORD.findall(value.lower()):
                if len(word) >= 4 and word not in _STOPWORDS:
                    terms.add(word)
        elif isinstance(value, Mapping):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                walk(item)

    walk(dict(puzzle.payload))
    walk(dict(puzzle.solution))
    return frozenset(terms)


class ShareRedactor:
    """The gate every rendered share passes before it leaves the engine."""

    def __init__(
        self,
        *,
        max_chars: int = MAX_SHARE_CHARS,
        max_lines: int = MAX_SHARE_LINES,
    ) -> None:
        self._max_chars = max_chars
        self._max_lines = max_lines

    def check(
        self,
        text: str,
        *,
        allowed_glyphs: Iterable[str],
        banned_ids: Iterable[str],
        where: str,
    ) -> None:
        if len(text) > self._max_chars:
            raise ShareLeak(
                f"{where} is {len(text)} characters, over the "
                f"{self._max_chars} cap; a share is a paste, not an export"
            )
        lines = text.splitlines()
        if len(lines) > self._max_lines:
            raise ShareLeak(
                f"{where} has {len(lines)} lines, over the {self._max_lines} cap"
            )

        allowed = set()
        for glyph in allowed_glyphs:
            allowed.update(glyph)

        for character in text:
            if character in allowed or character in _STRUCTURAL:
                continue
            if character.isalnum() and character.isascii():
                continue
            raise ShareLeak(
                f"{where} contains {character!r} (U+{ord(character):04X}), "
                "which is neither a declared token nor structural text"
            )

        lowered = text.lower()
        for identifier in banned_ids:
            if identifier.lower() in lowered:
                raise ShareLeak(
                    f"{where} contains an identifier; a share is posted in "
                    "public and must not carry one"
                )
        match = _RECORD_ID.search(lowered)
        if match is not None:
            raise ShareLeak(
                f"{where} contains what looks like a record id: {match.group(0)!r}"
            )
        match = _HEX_RUN.search(lowered)
        if match is not None:
            raise ShareLeak(
                f"{where} contains a long hex run, which is either a hash or "
                "a fingerprint and is neither one a player should post"
            )
    def check_words(
        self, text: str, banned_words: frozenset[str], *, where: str
    ) -> None:
        """Spoiler check, applied to game-supplied text only.

        The header, the counts line and the alt sentence are written by the
        engine from a vocabulary it controls, so running a spoiler check over
        them would only ever produce false positives: a puzzle whose prompt
        used the word "tries" would make the counts line unwritable. What does
        need checking is every fragment that came from the game.
        """
        for word in _WORD.findall(text.lower()):
            if word in banned_words:
                raise ShareLeak(
                    f"{where} contains {word!r}, which appears in the puzzle "
                    "or its solution; a share must not spoil the day"
                )


class ShareService:
    """Turns a game's semantic tokens into text a player can paste."""

    def __init__(
        self,
        *,
        redactor: ShareRedactor | None = None,
        brand: str = "PuzzleGen",
    ) -> None:
        self._redactor = redactor or ShareRedactor()
        self._brand = brand

    def build(
        self,
        session: SessionRecord,
        plugin: GamePlugin,
        descriptor: GameDescriptor,
        puzzle: Puzzle,
        *,
        difficulty_score: float,
    ) -> RenderedShare:
        """Render a finished session's share, or refuse to.

        Practice sessions produce nothing: a share says "here is how I did on
        today's puzzle", and a replay of a past day is not that.
        """
        if not session.is_terminal:
            raise ValueError(
                f"session {session.id} is still open; there is nothing to share yet"
            )
        if not session.ranked:
            raise ValueError(
                f"session {session.id} is a practice replay and has no share"
            )
        if not descriptor.share_tokens:
            raise ShareLeak(
                f"game {descriptor.game_id} declares no share tokens, so "
                "nothing it emits can be checked against a vocabulary"
            )

        telemetry = build_telemetry(
            session, puzzle, difficulty_score=difficulty_score
        )
        artifact = self._artifact(plugin, descriptor, puzzle, telemetry, session)
        grid_glyph, grid_plain, rows = self._grid(artifact, descriptor)

        header = f"{self._brand} {descriptor.display_name} {session.day_key}"
        summary = self._summary(session, artifact)
        text = "\n".join(part for part in (header, summary, grid_glyph) if part)
        text_plain = "\n".join(
            part for part in (header, summary, grid_plain) if part
        )
        alt_text = self._alt_text(descriptor, artifact, session, rows)

        banned_words = forbidden_terms(puzzle)
        banned_ids = (
            session.id,
            session.player_id,
            session.manifest_id,
            session.puzzle_id,
        )
        glyphs = [t.glyph for t in descriptor.share_tokens]
        plains = [t.plain for t in descriptor.share_tokens]
        self._redactor.check(
            text,
            allowed_glyphs=glyphs,
            banned_ids=banned_ids,
            where="share text",
        )
        self._redactor.check(
            text_plain,
            allowed_glyphs=plains,
            banned_ids=banned_ids,
            where="plain share text",
        )
        self._redactor.check(
            alt_text,
            allowed_glyphs=(),
            banned_ids=banned_ids,
            where="share alt text",
        )
        # Everything above is engine-written. These two are not, and they are
        # the only strings a game can push into a public surface.
        self._redactor.check_words(
            descriptor.display_name, banned_words, where="game display name"
        )
        self._redactor.check_words(
            artifact.outcome, banned_words, where="share outcome"
        )

        return RenderedShare(
            id=ids.for_share(session.id, SHARE_FORMAT_VERSION),
            game_id=descriptor.game_id,
            day_key=session.day_key,
            outcome=artifact.outcome,
            text=text,
            text_plain=text_plain,
            alt_text=alt_text,
            detail={
                "attempts": session.attempts,
                "hints_used": session.hints_used,
                "completed": session.state.value == "COMPLETED",
            },
        )

    # -- internals -----------------------------------------------------------

    def _artifact(
        self,
        plugin: GamePlugin,
        descriptor: GameDescriptor,
        puzzle: Puzzle,
        telemetry: Any,
        session: SessionRecord,
    ) -> ShareArtifact:
        try:
            artifact = plugin.create_share_artifact(
                puzzle, telemetry, session.day_key
            )
        except Exception as exc:  # noqa: BLE001 - plugin boundary
            raise ShareLeak(
                f"game {descriptor.game_id} failed to build a share artifact: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        if not isinstance(artifact, ShareArtifact):
            raise ShareLeak(
                f"game {descriptor.game_id} returned "
                f"{type(artifact).__name__} from create_share_artifact()"
            )
        if artifact.day_key != session.day_key:
            raise ShareLeak(
                f"game {descriptor.game_id} built a share for "
                f"{artifact.day_key} from a session on {session.day_key}"
            )
        return artifact

    def _grid(
        self, artifact: ShareArtifact, descriptor: GameDescriptor
    ) -> tuple[str, str, int]:
        """Resolve token names to glyphs, refusing any name never declared."""
        glyph_rows: list[str] = []
        plain_rows: list[str] = []
        for row in artifact.tokens:
            if len(row) > MAX_TOKENS_PER_ROW:
                raise ShareLeak(
                    f"game {descriptor.game_id} emitted a share row of "
                    f"{len(row)} tokens, over the {MAX_TOKENS_PER_ROW} cap"
                )
            glyphs: list[str] = []
            plains: list[str] = []
            for name in row:
                spec = descriptor.share_token(name)
                if spec is None:
                    raise ShareLeak(
                        f"share.undeclared_token: game {descriptor.game_id} "
                        f"emitted {name!r}, which is not in its declared "
                        "vocabulary; this is a plugin bug, not a content "
                        "problem"
                    )
                glyphs.append(spec.glyph)
                plains.append(spec.plain)
            glyph_rows.append("".join(glyphs))
            plain_rows.append("".join(plains))
        return "\n".join(glyph_rows), "\n".join(plain_rows), len(glyph_rows)

    def _summary(self, session: SessionRecord, artifact: ShareArtifact) -> str:
        """One line of counts, written by the engine rather than the game.

        Counts come from the ledger, so a game cannot publish a flattering
        attempt count, and the headline the game supplied is dropped: it is
        free text on a public surface and there is no way to check intent.
        """
        parts = [f"{session.attempts} tries"]
        if session.hints_used:
            parts.append(f"{session.hints_used} hints")
        seconds = session.elapsed_ms // 1000
        parts.append(f"{seconds // 60}m {seconds % 60:02d}s")
        return " / ".join(parts)

    def _alt_text(
        self,
        descriptor: GameDescriptor,
        artifact: ShareArtifact,
        session: SessionRecord,
        rows: int,
    ) -> str:
        outcome = (
            "solved" if session.state.value == "COMPLETED" else "not solved"
        )
        grid = (
            f" The grid has {rows} row{'s' if rows != 1 else ''}." if rows else ""
        )
        return (
            f"{descriptor.display_name} for {session.day_key}: {outcome} in "
            f"{session.attempts} tries with {session.hints_used} hints.{grid}"
        )


def token_legend(descriptor: GameDescriptor) -> tuple[ShareTokenSpec, ...]:
    """What each glyph in a share means, for a client that wants to explain
    its own share sheet without reimplementing the vocabulary."""
    return descriptor.share_tokens
