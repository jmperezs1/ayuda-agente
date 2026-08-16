"""
What the API shows by default.

Two constants, in one place because several endpoints have to agree on them. If the graph
draws an edge the match list refuses to show, the coordinator sees a line on the map with no
row behind it and concludes the dashboard is broken.

Note:
    Both leave out what a decision already closed — covered and expired requirements, failed
    and discarded matches. Surfacing those reopens a question somebody already answered.

    `unverified` is *in*, deliberately. It used to be excluded, which hid 90% of everything a
    live corpus found. Nothing is ever sent without a human clicking, so a weakly backed row
    costs a coordinator a glance rather than a wrong delivery — and the label is what lets
    them spend that glance well.
"""

from ayudagente.radar.choices import MatchStatus, RequirementStatus

# Live work, including the weakly backed
OPEN_REQUIREMENT_STATUSES = (
    RequirementStatus.OPEN,
    RequirementStatus.PARTIAL,
    RequirementStatus.UNVERIFIED,
)

# Matches nobody has ruled out yet
VISIBLE_MATCH_STATUSES = (
    MatchStatus.PROPOSED,
    MatchStatus.CONTACTED,
    MatchStatus.CONFIRMED,
    MatchStatus.DELIVERED,
)
