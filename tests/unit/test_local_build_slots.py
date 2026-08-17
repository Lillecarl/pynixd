"""`max-jobs` of the client limits the builds that pynixd runs itself.

`Worker::waitForBuildSlot` at `worker.cc:261` counts `getNrLocalBuilds()`
against `settings.maxBuildJobs`, and it starts no further local build until
one ends. A build on a backend costs no slot.

`_build_slots` of `goals/requests.py` limited the root goals of one request
already, and that is not the whole fan-out: a root goal realises the input
derivations of its derivation at the same time, and each one is a separate
build. `main:build` of the functional suite measured three builds together
under `-j1`. Issue #196.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pynixd.scheduler import Scheduler
from pynixd.serde import SetOptionsRequest
from pynixd.serde.ids import LOCAL_STORE_ID, StoreId

if TYPE_CHECKING:
    from pynixd.build_queue import QueuedBuild

_BUILDER = StoreId("builder1")


def _options(max_build_jobs: int) -> SetOptionsRequest:
    """A `SetOptions` request with every field, because no field has a default."""
    return SetOptionsRequest(
        keep_failed=False,
        keep_going=False,
        try_fallback=False,
        verbosity=0,
        max_build_jobs=max_build_jobs,
        max_silent_time=0,
        obsolete_use_build_hook=True,
        build_verbosity=0,
        obsolete_log_type=0,
        obsolete_print_build_trace=0,
        build_cores=1,
        use_substitutes=True,
        overrides={},
    )


class FakeStore:
    def __init__(self, store_id: StoreId) -> None:
        self.store_id = store_id


class FakeBuild:
    def __init__(self, options: SetOptionsRequest | None) -> None:
        self.options = options
        self.build_id = "build-1"


def _is_full(
    options: SetOptionsRequest | None,
    running: dict[StoreId, int],
    this_pass: dict[StoreId, int],
) -> bool:
    scheduler: Any = type("S", (), {"local_store": FakeStore(LOCAL_STORE_ID)})()
    return Scheduler._local_slot_is_full(  # noqa: SLF001 -- the limit is the unit under test
        scheduler,
        cast("QueuedBuild", FakeBuild(options)),
        running,
        this_pass,
    )


def test_one_job_leaves_no_slot_for_a_second_build() -> None:
    assert _is_full(_options(1), {LOCAL_STORE_ID: 1}, {}) is True


def test_one_job_leaves_the_first_slot_free() -> None:
    assert _is_full(_options(1), {}, {}) is False


def test_a_build_assigned_in_this_pass_takes_the_slot() -> None:
    """Two builds of one pass must not both take the single slot."""
    assert _is_full(_options(1), {}, {LOCAL_STORE_ID: 1}) is True


def test_four_jobs_leave_a_slot_while_three_run() -> None:
    assert _is_full(_options(4), {LOCAL_STORE_ID: 2}, {LOCAL_STORE_ID: 1}) is False
    assert _is_full(_options(4), {LOCAL_STORE_ID: 3}, {LOCAL_STORE_ID: 1}) is True


def test_zero_jobs_still_leaves_one_slot() -> None:
    """`max-jobs = 0` of Nix means "run no build here", and pynixd has no
    other place to run one. One at a time is the nearest answer, and
    `_build_slots` of `goals/requests.py` takes the same one."""
    assert _is_full(_options(0), {}, {}) is False
    assert _is_full(_options(0), {LOCAL_STORE_ID: 1}, {}) is True


def test_a_build_of_pynixd_itself_takes_no_limit() -> None:
    """No client asked for it, so no client named `max-jobs`."""
    assert _is_full(None, {LOCAL_STORE_ID: 99}, {}) is False


def test_a_build_on_a_backend_costs_no_local_slot() -> None:
    """The count of the backend does not reach the local limit."""
    assert _is_full(_options(1), {_BUILDER: 5}, {_BUILDER: 5}) is False
