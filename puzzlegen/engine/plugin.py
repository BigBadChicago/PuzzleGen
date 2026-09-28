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
from typing import Any, Protocol, runtime_checkable

from ..content.port import ContentPort
from ..content.query import ContentRequirement, ContentResult
from ..core.rng import DeterministicRng
from ..core.types import (
    DifficultyBand,
    UniquenessContract,
    VerificationCompleteness,
)
from ..core.versions import PLUGIN_PROTOCOL_VERSION


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


@dataclass(frozen=True, slots=True)
class Puzzle:
    """An assembled puzzle, before verification.

    ``payload`` is opaque to the engine: only the game knows what a board or a
    clue set means. ``solution`` is separate so the engine can strip it when
    publishing and when building share artifacts, which is how a solution
    leak is made structurally difficult rather than merely discouraged.
    """

    game_id: str
    payload: Mapping[str, Any]
    solution: Mapping[str, Any]
    fact_refs: tuple[str, ...]
    #: Presentation hints the engine passes to the shell untouched.
    presentation: Mapping[str, Any] = field(default_factory=dict)
    candidate_id: str = ""

    def __post_init__(self) -> None:
        if not self.fact_refs:
            raise ValueError(
                "an assembled puzzle must declare the graph records it used; "
                "without them blast-radius tracking is impossible"
            )
        if not self.payload:
            raise ValueError("an assembled puzzle must carry a payload")


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
    """A measured, not guessed, difficulty score."""

    score: float
    confidence: float = 0.5
    features: Mapping[str, float] = field(default_factory=dict)
    detail: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("difficulty score must lie on [0, 1]")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("difficulty confidence must lie on [0, 1]")
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


def build_port_context(
    port: ContentPort,
    requirements: Sequence[ContentRequirement],
) -> dict[str, ContentResult]:
    """Satisfy a game's declared requirements through its port."""
    return port.satisfy(requirements)
