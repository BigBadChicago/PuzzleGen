"""The plugin contract.

Everything in this module is part of the boundary between the engine and a
game, which means every type here is also the wire format for the out-of-
process transport that replaces the in-process call later. That is why these
are plain frozen dataclasses of JSON-representable values rather than richer
objects: a type that cannot be serialised cannot cross a process boundary, and
discovering that after two games are written is expensive.

A game supplies rules, candidates, assembly, verification, difficulty,
scoring, presentation and sharing. It supplies no storage, no queries, no
freshness, no provenance and no moderation, because those are properties of
the shared graph and must not differ per game.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..content.port import ContentPort
from ..content.query import ContentRequirement, ContentResult
from ..core.rng import DeterministicRng
from ..core.types import (
    DifficultyBand,
    DifficultyReduction,
    UniquenessContract,
    VerificationCompleteness,
)
from ..core.versions import PLUGIN_PROTOCOL_VERSION

# A witness is described here and built in ``engine.witness``, which reads a
# game's space through ``engine.coupling``. Imported for real, the protocol
# would depend at runtime on the measurement machinery whose shape it is only
# naming.
if TYPE_CHECKING:
    from .witness import DeductionWitness


@dataclass(frozen=True, slots=True)
class StateSymbol:
    """One visual state, carried by a shape and a word as well as a colour.

    Declared per game because only the game knows its states. Validated for
    distinctness on both the symbol and the label: two states sharing either
    one are indistinguishable to somebody reading shapes or hearing text,
    which is the failure this type exists to make impossible to ship.
    """

    #: Machine name of the state, e.g. "correct", "locked".
    name: str
    #: Glyph shown alongside or instead of colour.
    symbol: str
    #: What a screen reader says.
    label: str
    #: Optional colour hint for clients that use one. Never the only channel.
    colour: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.symbol or not self.label:
            raise ValueError("a state symbol needs a name, a symbol and a label")


@dataclass(frozen=True, slots=True)
class AccessibilityDeclaration:
    """What a game promises about how its puzzle can be operated.

    Declared rather than inferred. The engine supplies focus management,
    announcements and keyboard routing, but only the game knows what its
    elements mean, and a game that cannot describe its semantics cannot be
    made accessible by any amount of generic infrastructure.
    """

    #: Semantic element kinds the puzzle presents, e.g. ("tile", "group").
    element_kinds: tuple[str, ...] = ()
    #: Keyboard model name the engine should install.
    keyboard_model: str = "grid"
    #: Templates for state announcements, keyed by event name. Rendered by the
    #: engine so wording stays consistent across games.
    announcements: Mapping[str, str] = field(default_factory=dict)
    #: True when no information is conveyed by colour alone.
    colour_independent: bool = True
    #: True when the puzzle is fully operable without pointer input.
    keyboard_complete: bool = True
    minimum_target_px: int = 44
    #: Every visual state this game shows. Empty is permitted here and
    #: refused by the session layer's accessibility gate, so a phase 4 game
    #: still describes itself while an unplayable promise stays unplayable.
    state_symbols: tuple[StateSymbol, ...] = ()

    def __post_init__(self) -> None:
        names = [s.name for s in self.state_symbols]
        symbols = [s.symbol for s in self.state_symbols]
        labels = [s.label.strip().lower() for s in self.state_symbols]
        for field_name, values in (
            ("name", names),
            ("symbol", symbols),
            ("label", labels),
        ):
            if len(set(values)) != len(values):
                raise ValueError(
                    f"state symbols must have a distinct {field_name}; two "
                    "states sharing one are indistinguishable to somebody "
                    "reading shapes or hearing text"
                )

    def is_complete(self) -> tuple[bool, str]:
        if not self.element_kinds:
            return False, "no semantic element kinds declared"
        if not self.colour_independent:
            return False, "information conveyed by colour alone"
        if not self.keyboard_complete:
            return False, "puzzle not operable by keyboard"
        if self.minimum_target_px < 44:
            return False, "interactive targets below the minimum size"
        return True, ""


@dataclass(frozen=True, slots=True)
class DifficultyThresholds:
    """Where one game's difficulty scale cuts between bands.

    Declared per game rather than fixed engine-wide. A branching factor of 4
    is trivial in one game and punishing in another, so a single global scale
    would be comparable across games and wrong for all of them. The cost is
    that two games' bands are not directly comparable; the manifest records
    the thresholds, so a later analysis can renormalise if it ever needs to.
    """

    #: Ascending cut points between consecutive supported bands. A game
    #: supporting three bands declares two cut points.
    cutoffs: tuple[float, ...] = (0.34, 0.67)

    def __post_init__(self) -> None:
        if list(self.cutoffs) != sorted(self.cutoffs):
            raise ValueError("difficulty cutoffs must ascend")
        if any(not 0.0 < c < 1.0 for c in self.cutoffs):
            raise ValueError("difficulty cutoffs must lie strictly inside (0, 1)")
        if len(set(self.cutoffs)) != len(self.cutoffs):
            raise ValueError("difficulty cutoffs must be distinct")

    def band_for(
        self, score: float, bands: Sequence[DifficultyBand]
    ) -> DifficultyBand:
        """Which band a measured score falls into.

        Cut points are exclusive lower bounds on the harder side, so a score
        exactly on a cutoff lands in the easier band. Ties have to break
        somewhere and breaking easier is the kinder failure.
        """
        if len(self.cutoffs) != len(bands) - 1:
            raise ValueError(
                f"{len(bands)} bands need {len(bands) - 1} cutoffs, "
                f"got {len(self.cutoffs)}"
            )
        for index, cutoff in enumerate(self.cutoffs):
            if score <= cutoff:
                return bands[index]
        return bands[-1]


@dataclass(frozen=True, slots=True)
class ShareTokenSpec:
    """One abstract result token and its three renderings.

    A game emits token names; the engine turns them into characters. Three
    renderings are required rather than one: the glyph for the paste, the
    plain character for clients and readers that cannot handle the glyph, and
    the label for anything spoken. A share with only a glyph is a share that
    some players cannot read at all.
    """

    name: str
    glyph: str
    plain: str
    label: str

    def __post_init__(self) -> None:
        if not self.name or not self.glyph or not self.plain or not self.label:
            raise ValueError("a share token needs a name, glyph, plain and label")
        if len(self.plain) != 1 or not self.plain.isascii():
            raise ValueError("the plain rendering must be one ASCII character")


@dataclass(frozen=True, slots=True)
class GameDescriptor:
    """A game's static identity and contract."""

    game_id: str
    display_name: str
    game_version: str
    protocol_version: str = PLUGIN_PROTOCOL_VERSION
    uniqueness_contract: UniquenessContract = UniquenessContract.EXACTLY_ONE
    #: Required when the contract is EXACTLY_N.
    expected_solution_count: int | None = None
    supported_bands: tuple[DifficultyBand, ...] = (
        DifficultyBand.EASY,
        DifficultyBand.MEDIUM,
        DifficultyBand.HARD,
    )
    supported_locales: tuple[str, ...] = ("en",)
    #: What the game's own verifier promises. A game declaring COMPLETE and
    #: returning a partial enumeration is a protocol violation, not a content
    #: problem, so the promise is recorded here and checked against results.
    verification_completeness: VerificationCompleteness = (
        VerificationCompleteness.COMPLETE
    )
    #: Bound on the state space, required when verification is incomplete.
    search_bound: int | None = None
    accessibility: AccessibilityDeclaration = field(
        default_factory=AccessibilityDeclaration
    )
    difficulty_thresholds: DifficultyThresholds = field(
        default_factory=DifficultyThresholds
    )
    #: How many rounds of content questions this game asks (protocol 1.3).
    #:
    #: 1 is the old behaviour and the default: one declarative list, satisfied
    #: before the game runs. Above 1 the game also implements
    #: :class:`RefiningGame`, and each later round may depend on what the
    #: earlier ones returned, which is what a puzzle whose own shape is
    #: discovered while building it needs. Declared rather than discovered so
    #: the generation cost of a day is bounded before any game runs.
    content_rounds: int = 1
    #: Whether this game emits composite puzzles (protocol 1.3).
    #:
    #: A staged game implements :class:`StagedGame` and every board it
    #: assembles carries ``parts``; a game that is not staged may not emit
    #: them. Both halves are checked, because the interesting failure is a
    #: game that declares stages and ships a flat board: its stage gating
    #: would be verified against nothing.
    staged: bool = False
    #: How this game's difficulty axes reduce to its banded score (1.3).
    #:
    #: ``NONE`` means the game reports no axes and ``score`` stands alone.
    #: Anything else means it reports axes and the engine recomputes the score
    #: from them, so a game cannot measure three axes and then band against a
    #: fourth number nobody can see.
    difficulty_reduction: DifficultyReduction = DifficultyReduction.NONE
    #: Whether this game implements ``witness`` (protocol 1.2).
    #:
    #: Declared rather than discovered. A witness is the elimination a
    #: generator already did, kept instead of thrown away, and a game that
    #: builds its boards by search genuinely has none to emit: there is no
    #: reason sequence to hand over. So the absence is a fact about the game
    #: worth stating, and a caller that wants a witness can ask whether one
    #: exists without calling a method to find out.
    supplies_witness: bool = False
    #: The complete vocabulary this game's share artifacts may use. A token
    #: outside it is a protocol error, not a content rejection, which is what
    #: lets the engine hold a codepoint allowlist for share text.
    share_tokens: tuple[ShareTokenSpec, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.game_id or not self.game_id.replace("_", "").isalnum():
            raise ValueError(f"game_id must be a simple slug, got {self.game_id!r}")
        if self.game_id != self.game_id.lower():
            raise ValueError("game_id must be lowercase")
        if (
            self.uniqueness_contract is UniquenessContract.EXACTLY_N
            and self.expected_solution_count is None
        ):
            raise ValueError("EXACTLY_N requires expected_solution_count")
        if (
            self.verification_completeness is VerificationCompleteness.SOUND_INCOMPLETE
            and self.search_bound is None
        ):
            raise ValueError(
                "a game with incomplete verification must declare a search bound"
            )
        if not self.supported_bands:
            raise ValueError("a game must support at least one difficulty band")
        if not 1 <= self.content_rounds <= MAX_CONTENT_ROUNDS:
            raise ValueError(
                f"content_rounds must lie in 1..{MAX_CONTENT_ROUNDS}, got "
                f"{self.content_rounds}"
            )
        if self.staged and self.supplies_witness:
            # A witness's costs are counted over the board's unconstrained
            # space. On a staged board that space is the wrong denominator for
            # every stage after the first: stage two's statements are measured
            # against a space stage one has already cut, so they price as
            # nearly free and the ladder orders nothing. Per-stage witnesses
            # are specified and unbuilt (docs/architecture.md), so a staged
            # game declares no witness rather than declaring a mis-costed one.
            raise ValueError(
                "a staged game may not declare supplies_witness: a witness's "
                "costs are counted over one unconstrained space, which is the "
                "wrong space for every stage after the first"
            )
        if len(self.difficulty_thresholds.cutoffs) != len(self.supported_bands) - 1:
            raise ValueError(
                f"{len(self.supported_bands)} supported bands need "
                f"{len(self.supported_bands) - 1} difficulty cutoffs, got "
                f"{len(self.difficulty_thresholds.cutoffs)}"
            )
        if list(self.supported_bands) != sorted(
            self.supported_bands, key=list(DifficultyBand).index
        ):
            raise ValueError(
                "supported_bands must ascend in difficulty, since the "
                "thresholds are read positionally against them"
            )
        for field_name, values in (
            ("name", [t.name for t in self.share_tokens]),
            ("glyph", [t.glyph for t in self.share_tokens]),
            ("plain", [t.plain for t in self.share_tokens]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(
                    f"share tokens must have a distinct {field_name}; a "
                    "repeated one makes two outcomes look identical"
                )

    def share_token(self, name: str) -> ShareTokenSpec | None:
        for token in self.share_tokens:
            if token.name == name:
                return token
        return None

    def band_for(self, score: float) -> DifficultyBand:
        return self.difficulty_thresholds.band_for(score, self.supported_bands)


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Everything a game is given for one generation attempt.

    The RNG is the only source of randomness it may use, and it is a
    substream, so a game consuming a different amount than last time cannot
    disturb any other game's stream.
    """

    day_key: str
    game_version: str
    difficulty_target: DifficultyBand
    locale: str
    rng: DeterministicRng
    content: Mapping[str, ContentResult] = field(default_factory=dict)
    #: Generation budget: how many candidates the engine wants offered.
    candidate_budget: int = 64

    def result(self, name: str) -> ContentResult:
        try:
            return self.content[name]
        except KeyError as exc:
            raise KeyError(
                f"no content named {name!r}; the game must declare it in "
                "get_content_requirements before using it"
            ) from exc

    def derive(self, label: str) -> DeterministicRng:
        return self.rng.derive(label)


@dataclass(frozen=True, slots=True)
class PuzzleCandidate:
    """One proposal, before any engine gate has run.

    Candidates are cheap and most are discarded. A game should offer many;
    filtering is the engine's job and a game that pre-filters on freshness,
    confidence or policy is duplicating a gate it cannot see the inputs to.
    """

    candidate_id: str
    payload: Mapping[str, Any]
    #: Graph record ids this candidate rests on. Checked against the content
    #: port's ledger, so a fabricated id is caught before assembly.
    fact_refs: tuple[str, ...] = ()
    #: The game's own note about why it built this one. Carried into telemetry
    #: so a curator can tell which candidate shapes survive.
    rationale: str = ""

    def __post_init__(self) -> None:
        if not self.candidate_id:
            raise ValueError("a candidate must carry an id")


#: Ceiling on the rounds of content questions one generation may ask. Each
#: round is a real query against the snapshot under the game's port budget, so
#: the ceiling is what keeps a day's generation cost knowable in advance.
MAX_CONTENT_ROUNDS = 3


#: Ceiling on the stages one puzzle may declare. A board with more than this
#: many stages is not a harder puzzle, it is several puzzles, and the engine
#: verifies each stage twice: the cost is linear and the player's patience is
#: not.
MAX_PUZZLE_PARTS = 8


@dataclass(frozen=True, slots=True)
class PuzzlePart:
    """One stage of a composite puzzle, with its own answer and contract.

    A stage exists in the protocol rather than inside an opaque payload for
    one reason: every engine guarantee is written over a thing with an answer
    and a contract. A game that hid its stages in ``payload`` would still get
    uniqueness checked, but only for the whole board, and a board whose first
    stage has one answer and whose second has four passes a whole-board count
    of one. So the stages are declared, and each is checked as the puzzle it
    is.

    ``requires`` is the claim the engine checks and the reason this type is
    worth its cost. A part naming a predecessor is saying two things that are
    both measurable: that it is *not* decided before that predecessor is
    solved, and that it *is* decided afterwards. Both are verified in
    :mod:`puzzlegen.engine.staging`, so a chain is a fact about the board
    rather than an ordering in the interface.
    """

    part_id: str
    payload: Mapping[str, Any]
    solution: Mapping[str, Any]
    #: This stage's own contract, which need not be the board's. A board that
    #: is EXACTLY_ONE overall may have a stage that admits three orderings.
    uniqueness_contract: UniquenessContract = UniquenessContract.EXACTLY_ONE
    #: Required when this stage's contract is EXACTLY_N.
    expected_solution_count: int | None = None
    #: Stage ids that must be solved before this one is decided.
    requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.part_id or not self.part_id.replace("_", "").isalnum():
            raise ValueError(f"part_id must be a simple slug, got {self.part_id!r}")
        if not self.payload:
            raise ValueError(f"part {self.part_id!r} carries no payload")
        if not self.solution:
            raise ValueError(f"part {self.part_id!r} carries no solution")
        if (
            self.uniqueness_contract is UniquenessContract.EXACTLY_N
            and self.expected_solution_count is None
        ):
            raise ValueError(
                f"part {self.part_id!r} declares EXACTLY_N without a count"
            )
        if self.part_id in self.requires:
            raise ValueError(f"part {self.part_id!r} requires itself")
        if len(set(self.requires)) != len(self.requires):
            raise ValueError(f"part {self.part_id!r} repeats a requirement")

    @property
    def is_entry(self) -> bool:
        """Whether this stage is decided by its own clues alone."""
        return not self.requires

    def as_document(self) -> dict[str, Any]:
        """The stage as plain JSON, for storage and for hashing.

        One function rather than one per caller. A record, a rebuild check and
        a replay binding all have to agree byte for byte about what a stage
        is, and three serialisations would eventually be three answers.
        """
        return {
            "part_id": self.part_id,
            "payload": dict(self.payload),
            "solution": dict(self.solution),
            "uniqueness_contract": str(self.uniqueness_contract),
            "expected_solution_count": self.expected_solution_count,
            "requires": list(self.requires),
        }


def dependency_order(parts: Sequence[PuzzlePart]) -> tuple[PuzzlePart, ...]:
    """Stages in an order where every stage follows what it depends on.

    Deterministic where the dependencies leave a choice: among the stages
    whose requirements are all met, declaration order wins. Two runs of one
    generator must order a board's stages identically or the manifest hash
    moves without the board changing.

    Raises on a cycle, on a requirement naming no declared stage, and on a set
    of stages with no entry point, because each of those is a board that
    cannot be played rather than a board that is hard.
    """
    known = {part.part_id for part in parts}
    for part in parts:
        unknown = [r for r in part.requires if r not in known]
        if unknown:
            raise ValueError(
                f"part {part.part_id!r} requires {unknown}, which no part declares"
            )

    remaining = list(parts)
    solved: set[str] = set()
    ordered: list[PuzzlePart] = []
    while remaining:
        ready = [p for p in remaining if set(p.requires) <= solved]
        if not ready:
            stuck = sorted(p.part_id for p in remaining)
            raise ValueError(
                f"the stages {stuck} depend on each other in a cycle, so none "
                "of them is ever the next one to play"
            )
        for part in ready:
            ordered.append(part)
            solved.add(part.part_id)
        remaining = [p for p in remaining if p.part_id not in solved]
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class Puzzle:
    """An assembled puzzle, before verification.

    ``payload`` is opaque to the engine: only the game knows what a board or a
    clue set means. ``solution`` is separate so the engine can strip it when
    publishing and when building share artifacts, which is how a solution
    leak is made structurally difficult rather than merely discouraged.

    ``parts`` is empty for all but a staged game, and a staged game's board is
    still one puzzle with one ``solution``: the stages' answers are carried in
    it under their own ids, so everything downstream of verification, which is
    grading, sharing, retraction and the dependency commitment, reads one
    answer object and needs to know nothing about stages.
    """

    game_id: str
    payload: Mapping[str, Any]
    solution: Mapping[str, Any]
    fact_refs: tuple[str, ...]
    #: Presentation hints the engine passes to the shell untouched.
    presentation: Mapping[str, Any] = field(default_factory=dict)
    candidate_id: str = ""
    #: The stages this board is played in, empty for a single-stage board.
    parts: tuple[PuzzlePart, ...] = ()

    def __post_init__(self) -> None:
        if not self.fact_refs:
            raise ValueError(
                "an assembled puzzle must declare the graph records it used; "
                "without them blast-radius tracking is impossible"
            )
        if not self.payload:
            raise ValueError("an assembled puzzle must carry a payload")
        if not self.parts:
            return

        if len(self.parts) > MAX_PUZZLE_PARTS:
            raise ValueError(
                f"a puzzle may declare at most {MAX_PUZZLE_PARTS} stages, got "
                f"{len(self.parts)}"
            )
        ids = [part.part_id for part in self.parts]
        if len(set(ids)) != len(ids):
            raise ValueError("two stages share a part_id")
        # Raises on a cycle, an unknown requirement or no entry point.
        ordered = dependency_order(self.parts)
        if not any(part.is_entry for part in ordered):
            raise ValueError(
                "every stage depends on another, so the board has no first move"
            )
        for part in self.parts:
            if part.part_id not in self.solution:
                raise ValueError(
                    f"the board's solution does not carry stage "
                    f"{part.part_id!r}; a staged board's answer is the stages' "
                    "answers, keyed by stage, so nothing downstream has to "
                    "know what a stage is"
                )
            if dict(self.solution[part.part_id]) != dict(part.solution):
                raise ValueError(
                    f"the board's solution for stage {part.part_id!r} is not "
                    "the stage's own solution"
                )

    def ordered_parts(self) -> tuple[PuzzlePart, ...]:
        """The stages in playable order. Empty for a single-stage board."""
        if not self.parts:
            return ()
        return dependency_order(self.parts)

    def part(self, part_id: str) -> PuzzlePart:
        for candidate in self.parts:
            if candidate.part_id == part_id:
                return candidate
        raise KeyError(f"no stage {part_id!r} on this board")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """What a game's own solver found.

    The engine never takes ``solution_count`` on trust as evidence the
    uniqueness contract holds; it compares the count against the contract
    itself. A game cannot declare EXACTLY_ONE and publish two solutions,
    because the check is made against this number rather than against the
    game's opinion of it.
    """

    solvable: bool
    solution_count: int
    completeness: VerificationCompleteness = VerificationCompleteness.COMPLETE
    #: States enumerated. Recorded so an incomplete verification's bound can be
    #: checked against what the game declared.
    states_examined: int = 0
    #: Per-clue or per-constraint validity, for diagnosis.
    clue_validity: Mapping[str, bool] = field(default_factory=dict)
    #: Solutions found, for the engine to compare against the intended one.
    solutions: tuple[Mapping[str, Any], ...] = ()
    #: Set when ``solutions`` lists fewer than ``solution_count`` because the
    #: verifier stopped collecting. Without it the engine cannot tell a
    #: truncated list from an inconsistent one.
    solutions_truncated: bool = False
    #: Contract-specific evidence, e.g. how many longer alternative paths
    #: exist for UNIQUE_MINIMAL_PATH. Named metrics rather than extra fields
    #: so a new contract needs no change to this type.
    metrics: Mapping[str, float] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        if self.solution_count < 0:
            raise ValueError("solution_count cannot be negative")
        if self.solvable and self.solution_count == 0:
            raise ValueError("a solvable puzzle must have at least one solution")
        if not self.solvable and self.solution_count != 0:
            raise ValueError("an unsolvable puzzle cannot have solutions")
        if (
            self.solutions
            and not self.solutions_truncated
            and len(self.solutions) != self.solution_count
        ):
            raise ValueError(
                f"reported {self.solution_count} solutions but listed "
                f"{len(self.solutions)}; set solutions_truncated if the list "
                "is deliberately partial"
            )


@dataclass(frozen=True, slots=True)
class DifficultyMeasurement:
    """A measured, not guessed, difficulty score.

    ``axes`` is how a puzzle with more than one kind of work reports it.
    ``features`` are the inputs to a model; an axis is a difficulty in its own
    right, on the same [0, 1] scale as ``score``, and the game's declared
    :class:`~puzzlegen.core.types.DifficultyReduction` says how the axes make
    the one number the bands are read against. A staged board reports one axis
    per stage, so a punishing third stage cannot average away behind two easy
    ones.
    """

    score: float
    confidence: float = 0.5
    features: Mapping[str, float] = field(default_factory=dict)
    #: Separately measured difficulties, each on [0, 1]. Keyed by stage id on
    #: a staged board, by whatever the game names its kinds of work otherwise.
    axes: Mapping[str, float] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("difficulty score must lie on [0, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("difficulty confidence must lie on [0, 1]")
        for name, value in self.axes.items():
            if not name:
                raise ValueError("a difficulty axis must be named")
            if not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"difficulty axis {name!r} must lie on [0, 1], got {value}"
                )
        if not self.features:
            raise ValueError(
                "a difficulty measurement must expose the features it computed; "
                "an unexplained score cannot be audited or improved"
            )


@dataclass(frozen=True, slots=True)
class SessionTelemetry:
    """Standardised play signals every scoring function reads.

    Games score differently but all score from the same measured inputs, so a
    game cannot invent a favourable signal, and a score can be recomputed
    later from stored telemetry.
    """

    completed: bool
    attempts: int
    elapsed_ms: int
    mistakes: int
    hints_used: int
    #: Game-defined efficiency on [0, 1], e.g. moves against the optimum.
    efficiency: float = 1.0
    difficulty_score: float = 0.5
    events: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class Score:
    points: int
    breakdown: Mapping[str, int] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        if self.points < 0:
            raise ValueError("score cannot be negative")


@dataclass(frozen=True, slots=True)
class ShareArtifact:
    """Semantic result data. The engine formats the text."""

    game_id: str
    day_key: str
    outcome: str
    #: Abstract result tokens, e.g. rows of correct or incorrect markers.
    tokens: tuple[tuple[str, ...], ...] = ()
    headline: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MoveJudgement:
    """A game's ruling on one submitted move.

    Grading lives with the game because only the game knows what a partially
    correct answer is: a grouping puzzle's "three of four right" is not a
    comparison the engine could make against a solution hash. It runs engine
    side, never in the client, because the client is never given the solution.

    ``state`` is opaque game state after this move. The engine stores none of
    it: state is rebuilt by replaying the ledger, so a session stays a pure
    function of its moves and a cached state can never drift from them.
    """

    correct: bool
    #: True when this move finishes the puzzle, successfully or not.
    complete: bool = False
    #: True only when the puzzle was finished correctly.
    solved: bool = False
    state: Mapping[str, Any] = field(default_factory=dict)
    #: Shown to the player. Must not name an unrevealed part of the answer.
    note: str = ""

    def __post_init__(self) -> None:
        if self.solved and not self.complete:
            raise ValueError("a solved puzzle is a complete one")
        if self.solved and not self.correct:
            raise ValueError("the move that solves a puzzle is a correct move")


@dataclass(frozen=True, slots=True)
class Hint:
    """One step of help, supplied by the game and counted by the engine.

    ``cost`` is declared by the game and applied by the game's own scoring
    function; the engine only counts hints, because a hint's worth is part of
    a game's scoring model rather than a platform-wide constant.
    """

    text: str
    #: Element ids this hint reveals, so the shell can highlight them.
    reveals: tuple[str, ...] = ()
    cost: int = 0
    #: True when the game has no further hint to give.
    exhausted: bool = False

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("a hint must say something")
        if self.cost < 0:
            raise ValueError("hint cost must not be negative")


@dataclass(frozen=True, slots=True)
class PresentationModel:
    """A data description of the puzzle's interface, never markup."""

    elements: tuple[Mapping[str, Any], ...]
    layout: str = "grid"
    labels: Mapping[str, str] = field(default_factory=dict)
    state: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class GamePlugin(Protocol):
    """The eleven methods a game implements. Nothing else is called."""

    def describe(self) -> GameDescriptor: ...

    def get_content_requirements(
        self, *, difficulty_target: DifficultyBand, locale: str, day_key: str
    ) -> Sequence[ContentRequirement]:
        """What content this game needs, declaratively."""

    def generate_candidates(
        self, context: GenerationContext
    ) -> Sequence[PuzzleCandidate]:
        """Many proposals. Most will be rejected; that is expected."""

    def assemble(self, candidate: PuzzleCandidate, rng: DeterministicRng) -> Puzzle:
        """Turn one candidate into a concrete puzzle."""

    def verify(self, puzzle: Puzzle) -> VerificationResult:
        """Solve the game's own puzzle exhaustively, or soundly within a bound."""

    def measure_difficulty(
        self, puzzle: Puzzle, verification: VerificationResult
    ) -> DifficultyMeasurement: ...

    def score(self, puzzle: Puzzle, telemetry: SessionTelemetry) -> Score: ...

    def grade_move(
        self, puzzle: Puzzle, payload: Mapping[str, Any], state: Mapping[str, Any]
    ) -> MoveJudgement:
        """Rule on one submitted move, given the state the ledger replayed to."""

    def get_hint(
        self, puzzle: Puzzle, state: Mapping[str, Any], hints_used: int
    ) -> Hint:
        """Offer the next hint. ``hints_used`` is counted by the engine."""

    def render(
        self, puzzle: Puzzle, state: Mapping[str, Any], locale: str
    ) -> PresentationModel: ...

    def create_share_artifact(
        self, puzzle: Puzzle, telemetry: SessionTelemetry, day_key: str
    ) -> ShareArtifact: ...


@runtime_checkable
class WitnessingGame(Protocol):
    """A game that can emit the deduction its generator already did.

    A protocol of its own rather than a twelfth method on
    :class:`GamePlugin`, because ``GamePlugin`` is checked structurally: every
    game asserts it conforms, and folding an optional method into it would
    make four games that conform perfectly stop being plugins. The required
    surface stays required, and this names the capability beside it.

    A game implements this when it builds its boards by elimination and so has
    a reason sequence to hand over, and declares it in
    ``GameDescriptor.supplies_witness``. The two must agree: the declaration
    is what a caller reads, and the registry refuses a game whose declaration
    this protocol does not back up.
    """

    def witness(self, puzzle: Puzzle) -> "DeductionWitness":
        """The deductions this board's own clues force, cheapest first.

        What is returned is a claim the caller can check rather than a hint it
        has to believe: every statement's cost is counted over the board's
        unconstrained space, so
        :meth:`puzzlegen.engine.witness.DeductionWitness.holds_up` can
        re-derive the whole witness against that space without trusting the
        game that produced it.
        """
        ...


@runtime_checkable
class RefiningGame(Protocol):
    """A game that asks a second content question, informed by the first.

    One round of declarative requirements can only ask context-free
    questions: every query is written before the game has seen anything. That
    is enough for a board drawn from a pool and not enough for a board whose
    shape is discovered while building it, which is most of what "more
    complex" means in practice: a chain whose second link depends on which
    first link the snapshot actually had, a path through a relation, a lattice
    of categories that overlap in a way no query could predict.

    The game still never holds the port. It returns requirements and the
    engine satisfies them, through the same gates, the same ledger and the
    same budget as round one, which is what keeps a refined question from
    being a way around content governance.

    Declared in ``GameDescriptor.content_rounds``, which the engine reads
    before calling: a game that wants one round is never asked for two.
    """

    def refine_content_requirements(
        self,
        round_index: int,
        results: Mapping[str, ContentResult],
        *,
        difficulty_target: DifficultyBand,
        locale: str,
        day_key: str,
    ) -> Sequence[ContentRequirement]:
        """What else this game needs, given what it has.

        ``round_index`` counts from 2, since round one is the declarative
        list. Returning nothing ends the rounds early, which is the normal
        case when the first round happened to answer the question.

        A requirement whose name a previous round already used is refused by
        the engine rather than merged: two results under one name is a silent
        overwrite, and a game reading the overwritten one is reading content
        no gate knows it is using.
        """
        ...


@runtime_checkable
class StagedGame(Protocol):
    """A game whose boards are played in declared stages.

    The single method is a verifier that can be told what the player already
    knows. That is the whole mechanism: the engine runs it twice per gated
    stage, once knowing nothing and once knowing the predecessors' answers,
    and the pair of counts is what turns "stage two is unlocked by stage one"
    from a comment into a checked claim.

    Declared in ``GameDescriptor.staged``.
    """

    def verify_part(
        self,
        puzzle: Puzzle,
        part_id: str,
        known: Mapping[str, Mapping[str, Any]],
    ) -> VerificationResult:
        """Solve one stage, given the stages already solved.

        ``known`` maps stage id to that stage's answer, and holds only stages
        the caller has established. An empty mapping asks the honest question
        a player faces before touching the board: how many answers does this
        stage admit on its own clues? A stage that answers "one" to that and
        also declares a predecessor is a stage whose ordering is decoration,
        and the engine rejects the board for it.
        """
        ...


def build_port_context(
    port: ContentPort,
    requirements: Sequence[ContentRequirement],
) -> dict[str, ContentResult]:
    """Satisfy a game's declared requirements through its port."""
    return port.satisfy(requirements)
