"""
`get_balance` as an agent tool.

This is the orientation call. It answers "what is happening here" one level above the
individual rows, and its output is the vocabulary for the next call: every row carries the
`resource_key` that `match_resource` accepts, so the two chain without the model having
to invent a slug.
"""

from langchain_core.tools import tool

from agent_tools.get_balance.input import GetBalanceInput
from agent_tools.shared import (
    ToolInputError,
    get_event,
    resolve_place_arg,
    resolve_resource_arg,
)
from ayudagente.radar.services import get_balance as get_balance_service

# The whole point is a compact overview; past this it is a listing, not a summary
MAX_ROWS = 300


def serialize(row: dict) -> dict:
    """
    Round a balance row for reading, keeping the keys that chain into other tools.

    Returns:
        dict: Decimals become floats — a Decimal serializes as a string and the model then
            compares magnitudes as text.
    """
    return {
        "resource_key": row["resource_key"],
        "resource": row["resource"],
        "place": row["admin_unit"],
        "unit": row["unit"],
        "needed": float(row["needed"]),
        "offered": float(row["offered"]),
        "net": float(row["net"]),
        "needs": row["needs"],
        "offers": row["offers"],
        "unknown_quantity": row["unknown_quantity"],
    }


@tool("get_balance", args_schema=GetBalanceInput)
def get_balance(
    event_id: int,
    place: str | None = None,
    resource_key: str | None = None,
    only_deficits: bool = False,
) -> dict:
    """
    Summarize supply against demand per resource and place — start here.

    Each row compares what is needed with what is offered for one resource in one place and
    one unit. A negative `net` is a deficit, the gap somebody has to close. Use this before
    `match_resource` to see which resources are actually in play and to get the exact
    `resource_key` values that tool accepts.

    Quantities in different units are never added together, so the same resource can appear
    twice with different units. `unknown_quantity` counts the requirements that stated no
    amount at all: those contribute nothing to `needed`, so a row reading `needed: 0` with a
    non-zero `unknown_quantity` means "people are asking but nobody said how much", which is
    not the same as covered.

    Rows exclude requirements that are saturated, expired or whose time window has closed —
    the same filter `match_resource` applies, so the two agree.
    """
    try:
        event = get_event(event_id)
        resource = resolve_resource_arg(resource_key) if resource_key is not None else None
        admin_unit = resolve_place_arg(place, event.country_code) if place is not None else None
    except ToolInputError as exc:
        return {**exc.payload, "balance": []}

    rows = get_balance_service(event_id, resource=resource, admin_unit=admin_unit)
    if only_deficits:
        rows = [row for row in rows if row["net"] < 0]

    # Deficits first, deepest at the top: the ordering a coordinator would ask for
    rows.sort(key=lambda r: float(r["net"]))

    return {
        "count": min(len(rows), MAX_ROWS),
        "truncated": len(rows) > MAX_ROWS,
        "balance": [serialize(row) for row in rows[:MAX_ROWS]],
    }
