"""
The event's response network, built inside the tools that ask about it.

Built from the database on every call rather than read from the stored snapshot. The
snapshot is the map's serialization and can lag a rebuild; a coordinator asking whether a
shelter is already covered is asking about *now*, and an answer that is a few minutes old
sends a second donor to a place that no longer needs one.

Note:
    A carried match is a chain of three — offer → carrier → need — and enters the graph as
    two edges. Collapsing it into one hides the carrier, and the carrier is the actor whose
    loss ends the delivery, so it is exactly the one worth being able to see.
"""

from decimal import Decimal

import networkx as nx
from django.contrib.gis.geos import Point

from ayudagente.radar.choices import Direction, MatchStatus
from ayudagente.radar.models import ContactPoint, Match, Requirement
from ayudagente.radar.services import routable

# Matches that represent a real commitment on the ground, not a discarded idea
LIVE_MATCH_STATUSES = (
    MatchStatus.PROPOSED,
    MatchStatus.CONTACTED,
    MatchStatus.CONFIRMED,
    MatchStatus.DELIVERED,
)

# Beyond this a pair needs someone to carry it; mirrors the matching pass
DIRECT_REACH_KM = 30.0

URGENCY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


class Network:
    """
    One event's actors, what they need or offer, and who is already connected to whom.

    Note:
        `committed` holds only the fresh proposals, and that is the number that makes the
        whole thing honest. A requirement's `covered_quantity` counts matches a human
        already acted on, so a need with three untouched proposals still reads as
        completely uncovered — and the agent sends a fourth donor. Adding the proposals on
        top of it, rather than instead of it, is what stops that without double counting.
    """

    def __init__(self, event_id: int):
        self.event_id = event_id
        self.graph = nx.Graph()
        self.requirements: dict[int, Requirement] = {}
        self.by_actor: dict[int, list[Requirement]] = {}
        self.committed: dict[int, Decimal] = {}
        self.connected: dict[int, int] = {}
        self._contactable: set[int] | None = None
        self._load()

    def _load(self) -> None:
        rows = routable(Requirement.objects.filter(event_id=self.event_id)).select_related(
            "actor", "resource", "location", "location__admin_unit", "destination"
        )
        for requirement in rows:
            self.requirements[requirement.id] = requirement
            self.by_actor.setdefault(requirement.actor_id, []).append(requirement)
            # Every actor is a node, edges or not: an isolated one is the finding
            self.graph.add_node(requirement.actor_id)

        matches = Match.objects.filter(
            need__event_id=self.event_id, status__in=LIVE_MATCH_STATUSES
        ).select_related("need", "offer", "via_transport")

        for match in matches:
            # Only proposals: anything past `proposed` is already in `covered_quantity`
            if match.status == MatchStatus.PROPOSED:
                self.committed[match.need_id] = self.committed.get(match.need_id, Decimal("0")) + (
                    match.committed_quantity or Decimal("0")
                )
            self.connected[match.need_id] = self.connected.get(match.need_id, 0) + 1

            need_actor, offer_actor = match.need.actor_id, match.offer.actor_id
            if match.via_transport is None:
                self._link(offer_actor, need_actor, match)
                continue
            carrier = match.via_transport.actor_id
            self._link(offer_actor, carrier, match)
            self._link(carrier, need_actor, match)

    def _link(self, a: int, b: int, match: Match) -> None:
        if a == b:
            return
        self.graph.add_edge(a, b, match_id=match.id, resource=match.need.resource_id)

    def contactable(self) -> set[int]:
        """
        Actor ids we have some way of reaching, loaded once.

        Returns:
            set[int]: Used to tell "nobody is helping them" apart from "we cannot even
                tell them help exists", which are different problems with different fixes.
        """
        if self._contactable is None:
            self._contactable = set(
                ContactPoint.objects.filter(
                    actor__event_id=self.event_id, reachable=True
                ).values_list("actor_id", flat=True)
            )
        return self._contactable

    def still_needed(self, requirement: Requirement) -> float | None:
        """
        What is genuinely left over, after everything already promised.

        Returns:
            float | None: None when the amount was never stated — which is not zero, and
                must not be shown as covered.

        Note:
            Two layers, and they must not overlap. `outstanding_quantity` already subtracts
            `covered_quantity`, which the model derives from matches a human acted on; the
            fresh proposals nobody has touched yet are the part it does not know about, and
            they are what makes the difference between a listing and a recommendation.
        """
        outstanding = requirement.outstanding_quantity
        if outstanding is None:
            return None
        proposed = self.committed.get(requirement.id, Decimal("0"))
        return float(max(outstanding - proposed, Decimal("0")))

    def carriers_near(self, point: Point, km: float = DIRECT_REACH_KM) -> list[Requirement]:
        """Transport offers whose origin sits within reach of a point."""
        from ayudagente.radar.services.matching import _is_transport, geodesic_km

        return [
            r
            for r in self.requirements.values()
            if r.direction == Direction.OFFERS
            and _is_transport(r)
            and geodesic_km(r.location.point, point) <= km
        ]

    def reaches(self, a: int, b: int) -> bool:
        """True when some chain of live matches already connects two actors."""
        return a in self.graph and b in self.graph and nx.has_path(self.graph, a, b)
