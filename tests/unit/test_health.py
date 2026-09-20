"""`/healthz` has to answer two questions a TCP probe cannot.

A kubelet `tcpSocket` probe calls `connect()`, and the kernel completes it from
the listen backlog whether or not the application ever accepts. So it passes
against a wedged process, and fails only when a stall outlasts its timeout --
which a busy process also does. nixkube#37 and #53.

These test the verdict, not the endpoint: the report is what decides the status
code, and it is where the logic is.
"""

from __future__ import annotations

import time

import anyio

from pynixd.health import HealthReport, LoopLagMonitor


def test_a_report_names_the_check_that_failed() -> None:
    """A probe body that says only "unhealthy" moves the diagnosis elsewhere."""
    report = HealthReport(ok=False, checks={"ssh": "configured but not serving", "unix": "serving"})
    text = report.as_text()

    assert text.startswith("unhealthy")
    assert "ssh: configured but not serving" in text
    assert "unix: serving" in text


def test_a_healthy_report_still_lists_its_checks() -> None:
    """So an operator can see which interfaces were assessed, not just the verdict."""
    text = HealthReport(ok=True, checks={"ssh": "serving"}).as_text()

    assert text.startswith("ok")
    assert "ssh: serving" in text


async def test_the_monitor_records_a_stall_it_can_see() -> None:
    """A blocking sleep on the loop is what a transfer that never yields does.

    Gated on `started` and `wait_for_sample`, never on a duration. `start_soon`
    schedules a task and does not run one, so a test that sleeps to "let it
    start" is asserting on how fast this machine happens to be.
    """
    monitor = LoopLagMonitor(interval=0.01, window=30.0)

    async with anyio.create_task_group() as tg:
        tg.start_soon(monitor.run)
        await monitor.started.wait()
        # Block the loop. `time.sleep` is the point: nothing else can run,
        # which is the condition being measured.
        time.sleep(0.3)
        await monitor.wait_for_sample()
        monitor.stop()

    assert monitor.lifetime_max >= 0.2, f"missed a 300ms stall, saw {monitor.lifetime_max:.3f}s"


async def test_an_idle_loop_reports_almost_no_lag() -> None:
    """The negative control. Without it, a monitor that always reports a stall passes."""
    monitor = LoopLagMonitor(interval=0.01, window=30.0)

    async with anyio.create_task_group() as tg:
        tg.start_soon(monitor.run)
        await monitor.started.wait()
        for _ in range(5):
            await monitor.wait_for_sample()
        monitor.stop()

    assert monitor.lifetime_max < 0.1, f"idle loop reported {monitor.lifetime_max:.3f}s"


def test_the_window_forgets_an_old_stall() -> None:
    """A stall must not hold the process unhealthy once it is serving again.

    `lifetime_max` keeps it for the operator; `window_max` is what health asks,
    and it has to decay or one bad transfer restarts the pod forever.

    Time is moved, not waited for, so this asserts the decay rule rather than
    the machine's timing. No loop and no sampling: the samples are placed
    directly, because the rule under test is the pruning.
    """
    now = [1000.0]
    monitor = LoopLagMonitor(interval=0.01, window=10.0, clock=lambda: now[0])

    monitor._samples.append((now[0], 0.4))  # noqa: SLF001
    monitor.lifetime_max = 0.4
    assert monitor.window_max == 0.4

    now[0] += 9.0
    assert monitor.window_max == 0.4, "pruned a sample still inside the window"

    now[0] += 2.0
    assert monitor.window_max == 0.0, "the window kept a stall past its end"
    assert monitor.lifetime_max == 0.4, "lifetime_max must not decay"
