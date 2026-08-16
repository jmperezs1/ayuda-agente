"""
Saying how well a requirement is backed, and letting a person decide what that is worth.

This used to be a gate. An uncorroborated requirement was held out of matching entirely, and
against a live corpus that hid 90% of everything found — the gate was beating the product.

The gate was also unnecessary, and the reason is invariant 4. **Nothing is ever sent
automatically**: every message resolves to a link a human clicks, and `covered_quantity` only
counts once they have. So a wrong requirement cannot deliver anything, cannot saturate a real
need, and cannot reach a stranger. The worst it does is cost a coordinator a glance — and a
coordinator who can see *how well backed* a thing is spends that glance well.

So `unverified` marks rather than blocks. Everything is matched, everything is proposed, and
the label travels with it.

Note:
    Needs and offers still differ. A false need wastes a trip; a false offer makes a real need
    look covered — so the bar for calling an offer well-backed is higher, and the frontend has
    that difference to show.

    `text_image_conflict` overrides everything. When the photo does not match what the text
    claims, that is the signature of recycled imagery, and no follower count should outweigh
    it.
"""

import logging

from ayudagente.radar.choices import Direction, RequirementStatus, precisions_at_least
from ayudagente.radar.models import Requirement
from ayudagente.radar.models.actors import ORGANIZATION_KINDS

logger = logging.getLogger(__name__)

# Corroboration: the same thing said by this many separate posts
CORROBORATING_POSTS = 2

# What an actor's own credibility has to reach to stand alone, per direction
TRUSTED_FOR_NEEDS = 0.60
TRUSTED_FOR_OFFERS = 0.75

# Below this a place cannot be acted on anyway, so it cannot leave quarantine
MIN_PRECISION = "admin_2"


def verdict(requirement: Requirement, *, evidence_count: int | None = None) -> tuple[bool, str]:
    """
    Decide whether a requirement may be acted on.

    Args:
        requirement (Requirement): The need or offer to judge.
        evidence_count (int | None): Supplied by callers that already counted, to keep a
            batch pass from issuing one query per row.

    Returns:
        tuple[bool, str]: Whether it clears the bar, and the reason either way. The reason is
            stored so a coordinator asking "why is this greyed out" gets an answer.
    """
    actor = requirement.actor

    if requirement.location is None or not _precise_enough(requirement):
        return False, f"location is only {requirement.location and requirement.location.precision}"

    extraction = _reading(requirement)
    if extraction is not None and extraction.text_image_conflict:
        return False, "the photo does not match what the text claims"

    count = evidence_count if evidence_count is not None else requirement.evidence.count()
    if count >= CORROBORATING_POSTS:
        return True, f"corroborated by {count} separate posts"

    if actor.verified:
        return True, "the platform verifies this account"

    floor = TRUSTED_FOR_OFFERS if requirement.direction == Direction.OFFERS else TRUSTED_FOR_NEEDS
    if actor.credibility >= floor:
        return (
            True,
            f"credibility {actor.credibility:.2f} clears the bar for {requirement.direction}",
        )

    if requirement.direction == Direction.NEEDS and actor.kind in ORGANIZATION_KINDS:
        return True, "reported by an organisation"

    return False, "a single post from an account nothing corroborates"


def apply(requirement: Requirement, *, evidence_count: int | None = None) -> bool:
    """
    Label a requirement with how well it is backed, and say whether the label moved.

    Args:
        requirement (Requirement): The row to judge.
        evidence_count (int | None): Passed through to `verdict`.

    Returns:
        bool: True when the label changed.

    Note:
        Only ever moves between `open` and `unverified`. A requirement a human or the matching
        pass has already moved past those — covered, expired, discarded — is out of this
        function's hands, and quietly reopening one would undo a decision somebody made.

        `unverified` no longer removes anything from matching. It is what the frontend reads to
        show a proposal as weakly backed, and what a coordinator reads to decide.
    """
    if requirement.status not in (RequirementStatus.OPEN, RequirementStatus.UNVERIFIED):
        return False

    cleared, reason = verdict(requirement, evidence_count=evidence_count)
    target = RequirementStatus.OPEN if cleared else RequirementStatus.UNVERIFIED
    if requirement.status == target:
        return False

    requirement.status = target
    requirement.save(update_fields=["status"])
    logger.info("requirement %s -> %s: %s", requirement.pk, target, reason)
    return True


def _precise_enough(requirement: Requirement) -> bool:
    """
    Whether the place is exact enough to act on.

    Note:
        A `country` point is the centroid of a nation. Matching already refuses it, so a
        requirement carrying one can never be acted on however credible its author — letting
        it out of quarantine would only put a pin on the map where nothing is.
    """
    return requirement.location.precision in precisions_at_least(MIN_PRECISION)


def _reading(requirement: Requirement):
    """The extraction behind this requirement, or None when the evidence is gone."""
    observation = requirement.evidence.first()
    return getattr(observation, "extraction", None) if observation else None
