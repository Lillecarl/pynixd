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

import pytest

from pynixd.build_queue import BuildQueue
from pynixd.scheduler import Scheduler
from pynixd.serde import (
    BasicDerivation,
    BuildDerivationRequest,
    BuildDerivationResponse,
    BuildMode,
    BuildResult,
    BuildResultStatus,
    SetOptionsRequest,
    StorePath as SerdeStorePath,
)
from pynixd.serde.ids import LOCAL_STORE_ID, RequestId, StoreId

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
        # `DaemonStore.in_flight` answers `pool.active_connections`, and a
        # busy store reports many. A number well above any build count, so a
        # test that confuses the two cannot pass by accident.
        self.in_flight = 9


class FakeBuild:
    def __init__(self, options: SetOptionsRequest | None) -> None:
        self.options = options
        self.build_id = "build-1"
        self.is_building = False
        self.assigned_store_id: StoreId | None = None


def _running_build(store_id: StoreId) -> FakeBuild:
    build = FakeBuild(None)
    build.is_building = True
    build.assigned_store_id = store_id
    return build


def _pending_build() -> FakeBuild:
    return FakeBuild(None)


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


def test_the_count_that_reaches_the_limit_is_the_count_of_builds() -> None:
    """`_filter_schedulable` answers a build count for this, and two counts.

    `override_in_flight` is the other one, and it is
    `DaemonStore.in_flight`, which answers `pool.active_connections`. One
    `QueryPathInfo` raises that number. This limit read it first, so a single
    query filled the one slot of `-j1`, every build waited for a slot, and no
    build was running to end and trigger the next pass. The `ca` suite went
    from about a minute to more than ten.
    """
    pending = [
        _running_build(LOCAL_STORE_ID),
        _running_build(_BUILDER),
        _pending_build(),
    ]
    scheduler: Any = type(
        "S", (), {"stores": {LOCAL_STORE_ID: FakeStore(LOCAL_STORE_ID), _BUILDER: FakeStore(_BUILDER)}}
    )()

    _schedulable, override_in_flight, running_builds = Scheduler._filter_schedulable(  # noqa: SLF001 -- the two counts are the unit under test
        scheduler,
        cast("list[QueuedBuild]", pending),
    )

    assert running_builds == {LOCAL_STORE_ID: 1, _BUILDER: 1}
    # The connection count of the fake store is far above the build count, and
    # `max` keeps it. That is right for the ranker, and wrong for `max-jobs`.
    assert override_in_flight == {LOCAL_STORE_ID: 9, _BUILDER: 9}
    assert _is_full(_options(1), running_builds, {}) is True
    assert _is_full(_options(2), running_builds, {}) is False


# ── A build that no live request wants takes no slot. Issue #286 ─────


class _LocalStore:
    """Enough of the local store for `_assign_to_stores` to pick it."""

    def __init__(self) -> None:
        self.store_id = LOCAL_STORE_ID
        self.is_healthy = True
        self.draining = False
        self.in_flight = 0

    def supports_derivation(self, platform: str, features: set[str]) -> bool:
        del platform, features
        return True


class _AssignScheduler:
    """Enough of `Scheduler` for `_assign_to_stores` to run against a real queue.

    Every branch that could stop an assignment for another reason answers
    "go ahead", so a build that is not assigned was stopped by the rule under
    test and by nothing else.
    """

    def __init__(self, queue: BuildQueue) -> None:
        self.queue = queue
        self.local_store = _LocalStore()
        self.allocator = type("A", (), {"rank_stores": lambda *a, **k: []})()
        self.stores: dict[StoreId, object] = {}
        self.executed: list[object] = []

    def _has_compatible_store(self, build: object, features: set[str]) -> bool:
        del build, features
        return True

    def _local_slot_is_full(self, build: object, running: object, assigned: object) -> bool:
        del build, running, assigned
        return False

    async def execute_build(self, build: QueuedBuild, store: object) -> None:
        del store
        self.executed.append(build.build_id)


def _request(name: str) -> BuildDerivationRequest:
    return BuildDerivationRequest(
        drv_path=SerdeStorePath(path=f"/nix/store/0000000000000000000000000000000{name}-{name}.drv"),
        derivation=BasicDerivation(platform="x86_64-linux", builder=""),
        build_mode=BuildMode.NORMAL,
    )


async def _queue_with_a_failed_first_build(*, keep_going: bool) -> tuple[BuildQueue, QueuedBuild]:
    """x1 fails; the answer is the still-pending x2 of the same request."""
    queue = BuildQueue()
    request = RequestId(1)
    options = _options(1)
    options.keep_going = keep_going
    first, _ = await queue.enqueue(_request("1"), from_goal_path=True, goal_request_id=request, options=options)
    second, _ = await queue.enqueue(_request("2"), from_goal_path=True, goal_request_id=request, options=options)
    await queue.complete(
        first,
        BuildDerivationResponse(result=BuildResult(status=BuildResultStatus.PERMANENT_FAILURE)),
    )
    return queue, queue.by_id[second]


@pytest.mark.anyio
async def test_the_slot_a_failure_frees_does_not_go_to_the_same_request() -> None:
    """**This test stands for `build.sh:167` and `build.sh:176`.**

    `nix build -f fod-failing.nix -j1 -L` builds four fixed-output derivations
    that all give the wrong hash, and asserts one `error:` line naming x1. The
    scheduling pass that hands out the freed slot runs from the completion of
    x1, so without this rule x2 takes it. Issue #286.
    """
    queue, pending = await _queue_with_a_failed_first_build(keep_going=False)
    scheduler = _AssignScheduler(queue)

    waiting = await Scheduler._assign_to_stores(  # noqa: SLF001 -- the assignment is the unit under test
        cast("Any", scheduler),
        [pending],
        {},
        {},
    )

    assert scheduler.executed == []
    assert pending.build_task is None
    assert waiting == []
    # **And it is answered, not left pending.** A goal of the same request can
    # still be awaiting this future -- an input of a root that has not
    # answered -- and that root is what releases the build. Leaving it pending
    # deadlocks, and `main:build` measured that: build 12 of
    # `nix build -f fod-failing.nix -L x4` was skipped, never cancelled and
    # never completed, and the test hit its 300 s cap. Issue #286.
    assert pending.is_done
    # **And it says nothing to the client.** Nix reports nothing for the
    # waitees that `Goal::amDone` drops, and `build.sh:167` counts the
    # `error:` lines of the run. A reason here made that count three where
    # the test asserts one. `_cancel_locked` keeps the reason in the log.
    assert not pending.future.result().result.error_msg


@pytest.mark.anyio
async def test_keep_going_still_gives_the_slot_to_the_next_build() -> None:
    """`build.sh:180` asks for exactly this, and `build.sh:183` counts four errors."""
    queue, pending = await _queue_with_a_failed_first_build(keep_going=True)
    scheduler = _AssignScheduler(queue)

    await Scheduler._assign_to_stores(  # noqa: SLF001 -- same
        cast("Any", scheduler),
        [pending],
        {},
        {},
    )

    # The assignment is what the rule governs. `execute_build` runs in a task
    # that the loop has not stepped yet when `_assign_to_stores` returns, so
    # the task is the observable and `scheduler.executed` is not.
    assert pending.build_task is not None
    pending.build_task.cancel()
