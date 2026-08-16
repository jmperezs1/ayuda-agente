"""
Deterministic service layer: the functions the agent calls as tools.

The split follows the handbook's rule — judgment belongs to the model, functions belong
here. Everything in this package is plain Python over Postgres/PostGIS and OSRM, callable
from LangGraph tools, Celery tasks or a shell alike.
"""

from ayudagente.radar.services.actors import (
    best_contact_point,
    get_actor,
    get_contact_points,
)
from ayudagente.radar.services.comments import queue_comment_pulls, worth_reading
from ayudagente.radar.services.frontier import (
    create_harvest_job,
    get_frontier,
    record_actionable_find,
    record_harvest,
)
from ayudagente.radar.services.gazetteer import GazetteerError, load_country
from ayudagente.radar.services.graph import (
    build_graph_payload,
    input_fingerprint,
    refresh_graph,
)
from ayudagente.radar.services.harvest import (
    Harvested,
    HarvestNotConfigured,
    persist_items,
    run_harvest_job,
)
from ayudagente.radar.services.matching import (
    is_matchable_location,
    propose_match,
    run_matching_pass,
)
from ayudagente.radar.services.media import download_pending
from ayudagente.radar.services.outreach import draft_outreach, match_participants
from ayudagente.radar.services.promotion import promote_accounts, retire_exhausted
from ayudagente.radar.services.requirements import (
    find_admin_units,
    find_requirements,
    get_balance,
    resolve_resource,
    resource_catalog,
    resource_family,
    routable,
)
from ayudagente.radar.services.routing import RoutingError, plan_trip_stops, road_distance
from ayudagente.radar.services.sweep import bootstrap_event, places_by_zone, sweep_query

__all__ = [
    "GazetteerError",
    "HarvestNotConfigured",
    "Harvested",
    "RoutingError",
    "best_contact_point",
    "bootstrap_event",
    "build_graph_payload",
    "create_harvest_job",
    "download_pending",
    "draft_outreach",
    "find_admin_units",
    "find_requirements",
    "get_actor",
    "get_balance",
    "get_contact_points",
    "get_frontier",
    "input_fingerprint",
    "is_matchable_location",
    "load_country",
    "match_participants",
    "persist_items",
    "places_by_zone",
    "plan_trip_stops",
    "promote_accounts",
    "propose_match",
    "queue_comment_pulls",
    "record_actionable_find",
    "record_harvest",
    "refresh_graph",
    "resolve_resource",
    "resource_catalog",
    "resource_family",
    "retire_exhausted",
    "road_distance",
    "routable",
    "run_harvest_job",
    "run_matching_pass",
    "sweep_query",
    "worth_reading",
]
