"""KeyedBuildResult — a BuildResult paired with its DerivedPath primary key."""

from __future__ import annotations

from .build_result import BuildResult
from .derived_path import DerivedPath
from .wire_message import WireModel


class KeyedBuildResult(WireModel):
    """A BuildResult together with its DerivedPath key.

    Wire order: DerivedPath string then BuildResult fields.
    Both are WireModel subclasses — the generic engine writes them inline.
    """

    path: DerivedPath
    result: BuildResult
