"""The argument schema for `get_balance`."""

from pydantic import BaseModel, Field


class GetBalanceInput(BaseModel):
    """Arguments for `get_balance`. Descriptions here are what the model reads."""

    event_id: int = Field(description="Event to aggregate. Requirements never cross events.")
    place: str | None = Field(
        default=None,
        description=(
            "Narrow to an administrative area, by name ('Quibdó') or national code. It "
            "includes everything below it. Omit for the whole event."
        ),
    )
    resource_key: str | None = Field(
        default=None,
        description=(
            "Narrow to one resource family, as its slug or Spanish name. Omit to see every "
            "resource, which is the usual way to start."
        ),
    )
    only_deficits: bool = Field(
        default=False,
        description="Keep only rows where demand exceeds supply, the ones needing action.",
    )
