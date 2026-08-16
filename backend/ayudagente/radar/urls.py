"""
The API's routes.

Collections hang off their event, because every question in this system is asked about one
emergency. Single entities sit at the root: an id is globally unique, and nesting it would let
a frontend build a URL whose event and id disagree.
"""

from django.urls import path

from ayudagente.radar import agent_views, views

app_name = "radar"

urlpatterns = [
    path("events/", views.event_list, name="event-list"),
    path("events/<int:event_id>/", views.event_detail, name="event-detail"),
    path("events/<int:event_id>/graph/", views.event_graph, name="event-graph"),
    path("events/<int:event_id>/jobs/", views.job_list, name="job-list"),
    path("events/<int:event_id>/loop/", views.loop_status, name="loop-status"),
    path("events/<int:event_id>/actors/", views.actor_list, name="actor-list"),
    path(
        "events/<int:event_id>/requirements/",
        views.requirement_list,
        name="requirement-list",
    ),
    path("events/<int:event_id>/matches/", views.match_list, name="match-list"),
    path("events/<int:event_id>/outreach/", views.outreach_list, name="outreach-list"),
    path(
        "events/<int:event_id>/observations/",
        views.observation_list,
        name="observation-list",
    ),
    path("actors/<int:actor_id>/", views.actor_detail, name="actor-detail"),
    path(
        "requirements/<int:requirement_id>/",
        views.requirement_detail,
        name="requirement-detail",
    ),
    path(
        "observations/<int:observation_id>/",
        views.observation_detail,
        name="observation-detail",
    ),
    path(
        "outreach/<int:outreach_id>/dispatch/",
        views.dispatch_outreach,
        name="outreach-dispatch",
    ),
    path("resource-types/", views.resource_type_list, name="resource-type-list"),
    path("agent/coordination/", agent_views.coordination_agent, name="agent-coordination"),
    path("agent/frontier/", agent_views.frontier_agent, name="agent-frontier"),
]
