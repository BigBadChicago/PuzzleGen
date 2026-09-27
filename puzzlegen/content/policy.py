"""Content policy.

One place decides whether a record may appear in a puzzle. A game asks
``is_eligible``; it never implements its own filtering, because a term blocked
for one game and allowed in another is the failure this module exists to
prevent.

The central rule: a game policy may only narrow the platform policy. A game
can refuse content the platform allows; it can never admit content the
platform blocked. Without that asymmetry, adding a game would be a way to
route around moderation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..core.types import FreshnessClass, ProvenanceClass, ReviewStatus
from .similarity import TaxonomyIndex


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


@dataclass(frozen=True, slots=True)
class EligibilityVerdict:
    outcome: PolicyOutcome
    reason: str = ""
    #: Which policy layer decided. Named so a curator can tell a platform-wide
    #: block from one game's narrower rule without reading any code.
    source: str = "platform"

    @property
    def allowed(self) -> bool:
        return self.outcome is PolicyOutcome.ALLOW


@dataclass(frozen=True)
class ContentPolicy:
    """A set of rules about which records may be used."""

    name: str = "platform"
    #: Records refused outright, by internal id.
    denied_ids: frozenset[str] = frozenset()
    #: Categories whose entire subtree is refused. Subtree rather than exact
    #: match, because blocking a category and leaving its children usable
    #: would be a block in name only.
    denied_categories: frozenset[str] = frozenset()
    #: When non-empty, only these categories and their subtrees are usable.
    allowed_categories: frozenset[str] = frozenset()
    denied_predicates: frozenset[str] = frozenset()
    #: Case-insensitive substrings refused in any name or alias.
    denied_terms: frozenset[str] = frozenset()
    #: Full-match patterns, for shapes a substring list cannot express.
    denied_patterns: tuple[str, ...] = ()
    minimum_confidence: float = 0.0
    #: Provenance classes refused regardless of confidence.
    denied_provenance_classes: frozenset[ProvenanceClass] = frozenset()
    #: Freshness classes refused, for games that cannot tolerate churn.
    denied_freshness_classes: frozenset[FreshnessClass] = frozenset()
    allowed_taxonomies: frozenset[str] = frozenset()
    required_status: frozenset[ReviewStatus] = frozenset({ReviewStatus.ACTIVE})

    _compiled: tuple[re.Pattern[str], ...] = field(
        default=(), init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_compiled",
            tuple(re.compile(p, re.IGNORECASE) for p in self.denied_patterns),
        )

    def narrow(self, other: "ContentPolicy") -> "ContentPolicy":
        """Combine with a stricter policy, keeping only the stricter rule.

        Every field either unions (denials) or takes the tighter bound
        (thresholds, allow lists), so the result can never permit something
        either input refused. This is what makes a game policy incapable of
        unblocking platform-blocked content.
        """
        def tighter_allow(a: frozenset[str], b: frozenset[str]) -> frozenset[str]:
            # An empty allow list means "no restriction", so it yields to a
            # non-empty one; two non-empty lists intersect.
            if not a:
                return b
            if not b:
                return a
            return a & b

        return ContentPolicy(
            name=f"{self.name}+{other.name}",
            denied_ids=self.denied_ids | other.denied_ids,
            denied_categories=self.denied_categories | other.denied_categories,
            allowed_categories=tighter_allow(
                self.allowed_categories, other.allowed_categories
            ),
            denied_predicates=self.denied_predicates | other.denied_predicates,
            denied_terms=self.denied_terms | other.denied_terms,
            denied_patterns=tuple(
                dict.fromkeys((*self.denied_patterns, *other.denied_patterns))
            ),
            minimum_confidence=max(self.minimum_confidence, other.minimum_confidence),
            denied_provenance_classes=(
                self.denied_provenance_classes | other.denied_provenance_classes
            ),
            denied_freshness_classes=(
                self.denied_freshness_classes | other.denied_freshness_classes
            ),
            allowed_taxonomies=tighter_allow(
                self.allowed_taxonomies, other.allowed_taxonomies
            ),
            required_status=(
                self.required_status & other.required_status
                if self.required_status and other.required_status
                else self.required_status or other.required_status
            ),
        )

    def blocks_term(self, text: str) -> str | None:
        lowered = text.lower()
        for term in sorted(self.denied_terms):
            if term.lower() in lowered:
                return f"contains denied term {term!r}"
        for pattern in self._compiled:
            if pattern.search(text):
                return f"matches denied pattern {pattern.pattern!r}"
        return None


#: The platform's own floor. Deliberately minimal in code: real deny lists are
#: content, loaded from a policy file, not constants recompiled into a release.
DEFAULT_PLATFORM_POLICY = ContentPolicy(
    name="platform",
    minimum_confidence=0.7,
    required_status=frozenset({ReviewStatus.ACTIVE}),
)


class PolicyService:
    """Answers eligibility questions for every layer above it."""

    def __init__(
        self,
        platform_policy: ContentPolicy | None = None,
        taxonomy: TaxonomyIndex | None = None,
    ) -> None:
        self._platform = platform_policy or DEFAULT_PLATFORM_POLICY
        self._taxonomy = taxonomy
        self._game_policies: dict[str, ContentPolicy] = {}

    @property
    def platform_policy(self) -> ContentPolicy:
        return self._platform

    def register_game_policy(self, game_id: str, policy: ContentPolicy) -> ContentPolicy:
        """Register a game's policy, narrowed against the platform's.

        The stored result is always the narrowed combination, so there is no
        code path that consults a game policy on its own.
        """
        combined = self._platform.narrow(replace(policy, name=game_id))
        self._game_policies[game_id] = combined
        return combined

    def policy_for(self, game_id: str | None = None) -> ContentPolicy:
        if game_id is None:
            return self._platform
        return self._game_policies.get(game_id, self._platform)

    # -- eligibility ------------------------------------------------------

    def is_eligible(
        self,
        record,
        *,
        game_id: str | None = None,
        category_ids: Sequence[str] = (),
    ) -> EligibilityVerdict:
        """Whether one record may be used, under the effective policy."""
        policy = self.policy_for(game_id)
        layer = "platform" if game_id is None else game_id

        if record.id in policy.denied_ids:
            return EligibilityVerdict(PolicyOutcome.BLOCK, "explicitly denied", layer)

        if policy.required_status and record.status not in policy.required_status:
            return EligibilityVerdict(
                PolicyOutcome.BLOCK, f"status {record.status} not usable", layer
            )

        if record.confidence < policy.minimum_confidence:
            return EligibilityVerdict(
                PolicyOutcome.BLOCK,
                f"confidence {record.confidence:.2f} below "
                f"{policy.minimum_confidence:.2f}",
                layer,
            )

        classes = record.provenance_classes()
        blocked_classes = classes & policy.denied_provenance_classes
        if blocked_classes:
            return EligibilityVerdict(
                PolicyOutcome.BLOCK,
                f"provenance class denied: {sorted(blocked_classes)}",
                layer,
            )

        if record.freshness_class in policy.denied_freshness_classes:
            return EligibilityVerdict(
                PolicyOutcome.BLOCK,
                f"freshness class {record.freshness_class} denied",
                layer,
            )

        taxonomy = getattr(record, "taxonomy", None)
        if (
            taxonomy is not None
            and policy.allowed_taxonomies
            and taxonomy not in policy.allowed_taxonomies
        ):
            return EligibilityVerdict(
                PolicyOutcome.BLOCK, f"taxonomy {taxonomy!r} not allowed", layer
            )

        predicate = getattr(record, "predicate", None)
        if predicate is not None and predicate in policy.denied_predicates:
            return EligibilityVerdict(
                PolicyOutcome.BLOCK, f"predicate {predicate!r} denied", layer
            )

        for text in self._texts_of(record):
            reason = policy.blocks_term(text)
            if reason:
                return EligibilityVerdict(PolicyOutcome.BLOCK, reason, layer)

        verdict = self._check_categories(policy, record, category_ids, layer)
        if verdict is not None:
            return verdict

        return EligibilityVerdict(PolicyOutcome.ALLOW, "", layer)

    def _texts_of(self, record) -> Iterable[str]:
        name = getattr(record, "canonical_name", None)
        if name:
            yield name
        for alias in getattr(record, "aliases", ()):
            yield alias
        value = getattr(record, "value", None)
        if value is not None and getattr(value, "text", None):
            yield value.text

    def _check_categories(
        self,
        policy: ContentPolicy,
        record,
        category_ids: Sequence[str],
        layer: str,
    ) -> EligibilityVerdict | None:
        """Apply category allow and deny lists, including subtrees."""
        if not policy.denied_categories and not policy.allowed_categories:
            return None

        own = getattr(record, "id", "")
        candidates = list(category_ids)
        if own.startswith("category:"):
            candidates.append(own)
        if not candidates:
            # An allow list with nothing to check against would silently admit
            # every uncategorised record, which is the opposite of an allow
            # list's purpose.
            if policy.allowed_categories:
                return EligibilityVerdict(
                    PolicyOutcome.BLOCK,
                    "allow list in force and record has no category",
                    layer,
                )
            return None

        for category_id in candidates:
            if self._within(category_id, policy.denied_categories):
                return EligibilityVerdict(
                    PolicyOutcome.BLOCK, f"category {category_id} denied", layer
                )

        if policy.allowed_categories and not any(
            self._within(cid, policy.allowed_categories) for cid in candidates
        ):
            return EligibilityVerdict(
                PolicyOutcome.BLOCK, "no category on the allow list", layer
            )
        return None

    def _within(self, category_id: str, listed: frozenset[str]) -> bool:
        if category_id in listed:
            return True
        if self._taxonomy is None:
            return False
        ancestors = self._taxonomy.ancestors(category_id)
        return bool(ancestors & listed)

    # -- bulk -------------------------------------------------------------

    def filter_eligible(
        self,
        records: Sequence,
        *,
        game_id: str | None = None,
        categories_of=None,
    ) -> tuple[list, dict[str, int]]:
        """Split records into allowed and a count of block reasons.

        Reasons are returned as counts rather than logged, because generation
        telemetry needs to report why a day's candidate pool collapsed.
        """
        kept: list = []
        reasons: dict[str, int] = {}
        for record in records:
            category_ids = categories_of(record) if categories_of else ()
            verdict = self.is_eligible(
                record, game_id=game_id, category_ids=category_ids
            )
            if verdict.allowed:
                kept.append(record)
            else:
                reasons[verdict.reason] = reasons.get(verdict.reason, 0) + 1
        return kept, reasons
