"""Test subsumption: sort by coverage breadth, skip subsumed tests."""

from __future__ import annotations

import pytest

from tests._conftest.constants import _covered_features_key
from tests.test_features import TestFeatures


def _sort_by_subsumption(items: list[pytest.Item]) -> None:
    """Sort tests so broader-coverage tests run first.

    Tests with a ``covers`` marker are sorted by descending popcount
    (number of feature flags set).  Tests without the marker are placed
    at the end (no subsumption benefit).
    """

    def _popcount(item: pytest.Item) -> int:
        marker = item.get_closest_marker("covers")
        if marker is not None and marker.args:
            features: TestFeatures = marker.args[0]
            return len(features)
        return 0

    items.sort(key=_popcount, reverse=True)


def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> object:
    """Skip a test if all its features are already covered by passing tests.

    Only active when ``--no-test-subsumption`` is NOT set.
    Tests without a ``covers`` marker always run.
    """
    if item.config.getoption("no_test_subsumption"):
        return None

    marker = item.get_closest_marker("covers")
    if marker is None or not marker.args:
        return None

    features: TestFeatures = marker.args[0]
    covered = item.config.stash.get(_covered_features_key, TestFeatures(0))

    if features and features in covered:
        item.add_marker(pytest.mark.skip(reason="subsumed by broader tests (features already covered)"))

    return None
