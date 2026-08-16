"""What every dataset has to provide to be loadable and clearable."""

from collections.abc import Callable
from dataclasses import dataclass, field

Writer = Callable[[str], None]  # progress sink: `stdout.write`, `print` or a test collector

Counts = dict[str, int]  # what a load created, keyed however the seed wants to report it


@dataclass(frozen=True)
class Seed:
    """
    One loadable dataset.

    Attributes:
        name (str): What `--only` matches on.
        description (str): Shown by `--list`.
        event_names (tuple[str, ...]): The events this seed owns. Clearing deletes them and
            everything else cascades, which is why a seed must keep all its rows under
            events it created.
        load (Callable): Loads the dataset and reports what it created. Must be idempotent:
            a second run creates nothing and reports zeros.
        clear (Callable | None): Only needed when a seed creates rows outside its events —
            catalog entries, for instance — which a cascading delete would leave behind.
    """

    name: str
    description: str
    event_names: tuple[str, ...]
    load: Callable[[Writer], Counts] = field(repr=False)
    clear: Callable[[Writer], int] | None = field(default=None, repr=False)
