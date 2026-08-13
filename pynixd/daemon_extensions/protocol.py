"""Wire types owned by pynixd's private daemon extensions."""

from enum import IntEnum


class PynixdGCAction(IntEnum):
    """Actions accepted by pynixd's private collect-garbage operation."""

    DRY_RUN = 0
    EXECUTE = 1
