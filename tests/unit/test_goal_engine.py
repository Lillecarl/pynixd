"""Unit tests for request-local goal engine entrypoint helpers."""

from __future__ import annotations

import pytest

from pynixd.goals.engine import _require_normal_build_mode
from pynixd.serde import BuildMode


def test_require_normal_build_mode_accepts_normal() -> None:
    _require_normal_build_mode(BuildMode.NORMAL)


@pytest.mark.parametrize("build_mode", [BuildMode.CHECK, BuildMode.REPAIR, 99])
def test_require_normal_build_mode_rejects_unsupported_modes(build_mode: int) -> None:
    with pytest.raises(RuntimeError, match=r"only supports BuildMode.NORMAL"):
        _require_normal_build_mode(build_mode)
