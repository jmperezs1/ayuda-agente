"""
Scoring how much weight an actor's claims deserve.

`Actor.credibility` has existed since the first migration, defaults to 0.5, and until now
nothing ever wrote it — every actor in a live run scored exactly 0.5, which made it useless
as a signal at precisely the moment one was needed. The inputs were there the whole time:
`Observation` snapshots the author's follower count and verification badge, and 696 of 819
harvested posts carried both.

Note:
    Credibility is about *whose claim this is*, never about whether the claim is true. A
    verified ministry can post something wrong and an anonymous neighbour can be the only
    person reporting a real collapse. What it buys is an ordering for the cases nothing else
    settles, and a floor under what reaches a coordinator unreviewed.

    Deliberately coarse. The follower curve is logarithmic because the difference between two
    hundred and two thousand followers says something and the difference between two hundred
    thousand and a million says almost nothing, and a linear scale would let one large account
    outrank every local source in a department.
"""

import logging
import math

from ayudagente.radar.choices import CredibilitySource
from ayudagente.radar.models import Actor, Observation
from ayudagente.radar.models.actors import ORGANIZATION_KINDS

logger = logging.getLogger(__name__)

BASELINE = 0.5

# A verified badge is the strongest cheap signal a platform hands over
VERIFIED_BONUS = 0.25

# An account the platform recognises as an institution, weighted below its own badge
ORGANIZATION_BONUS = 0.10

# Followers at which the audience bonus saturates
FOLLOWER_CEILING = 100_000
FOLLOWER_WEIGHT = 0.20

FLOOR, CAP = 0.1, 0.95


def score(actor: Actor, observation: Observation, is_author: bool = False) -> tuple[float, str]:
    """
    Rate one actor from what the platform said about its author.

    Args:
        actor (Actor): The entity the mention resolved to.
        observation (Observation): The post it was mentioned in, carrying the author snapshot.
        is_author (bool): Whether the model read this entity as the account that posted.

    Returns:
        tuple[float, str]: The score in [0.1, 0.95] and the `CredibilitySource` that dominated
            it, so a surprising number can be explained rather than argued with.

    Note:
        An actor mentioned in someone else's post inherits nothing from that author. "La
        Alcaldía tiene un acopio" posted by a stranger says nothing about the mayor's office
        and everything about the stranger, so only the author of a post scores from it.
    """
    if not (is_author or _handle_matches(actor, observation)):
        return _from_kind(actor)

    value = BASELINE
    source = CredibilitySource.NONE

    if observation.author_verified:
        value += VERIFIED_BONUS
        source = CredibilitySource.VERIFIED

    followers = observation.author_followers or 0
    if followers > 0:
        value += FOLLOWER_WEIGHT * _reach(followers)
        if source == CredibilitySource.NONE:
            source = CredibilitySource.FOLLOWERS

    if actor.kind in ORGANIZATION_KINDS:
        value += ORGANIZATION_BONUS
        if source == CredibilitySource.NONE:
            source = CredibilitySource.OFFICIAL_ENTITY

    return _bounded(value), source


def refresh(actor: Actor, observation: Observation, is_author: bool = False) -> bool:
    """
    Store an actor's score, keeping the best evidence seen so far.

    Args:
        actor (Actor): The entity to score.
        observation (Observation): The post that supplied the author snapshot.
        is_author (bool): Whether the model read this entity as the account that posted.

    Returns:
        bool: True when the score changed.

    Note:
        Monotonic on purpose. The same organisation posts from a verified account one day and
        is mentioned in a stranger's reply the next, and letting the second overwrite the first
        would make credibility depend on which post happened to be read last.
    """
    value, source = score(actor, observation, is_author)
    if value <= actor.credibility:
        return False

    actor.credibility = value
    actor.credibility_source = source
    if observation.author_followers and (is_author or _handle_matches(actor, observation)):
        actor.max_followers = max(actor.max_followers or 0, observation.author_followers)

    actor.save(update_fields=["credibility", "credibility_source", "max_followers"])
    return True


def _handle_matches(actor: Actor, observation: Observation) -> bool:
    """
    Whether the actor's name is literally the posting handle.

    Note:
        A cheap corroboration of what the model already says, not the primary signal. It
        almost never fires — an organisation's name and its handle rarely match as strings —
        which is exactly why `is_author` had to come from the reading instead.
    """
    handle = (observation.author_handle or "").lstrip("@").casefold()
    if not handle:
        return False

    names = [actor.canonical_name, *actor.alternate_names]
    return any(handle == name.lstrip("@").casefold() for name in names if name)


def _from_kind(actor: Actor) -> tuple[float, str]:
    """What an actor is worth when the post that named it was written by somebody else."""
    if actor.kind in ORGANIZATION_KINDS:
        return _bounded(BASELINE + ORGANIZATION_BONUS), CredibilitySource.OFFICIAL_ENTITY
    return BASELINE, CredibilitySource.NONE


def _reach(followers: int) -> float:
    """
    Audience as a fraction of the ceiling, on a log curve.

    Note:
        Logarithmic because the step from two hundred to two thousand followers is meaningful
        and the step from two hundred thousand to a million is not. Linear, one national
        newspaper would outrank every local source in an affected department.
    """
    return min(1.0, math.log10(followers + 1) / math.log10(FOLLOWER_CEILING))


def _bounded(value: float) -> float:
    """
    Keep a score inside its range.

    Note:
        Never reaches 1.0. A platform badge is evidence about an account, not proof about a
        claim, and a score of one would invite code that treats it as certainty.
    """
    return round(min(max(value, FLOOR), CAP), 3)
