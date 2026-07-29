"""The scheduler's safety property, tested without a single socket.

Before the refactor this behaviour was reachable only by starting real servers.
It is now a pure function over a closed sum, so the case that actually matters --
an unreachable peer must never be read as "the work is finished" -- is a
two-line assertion.
"""

from __future__ import annotations

import threading

from ci.domain import (
    BuildFailed,
    BuildSucceeded,
    PeerEmpty,
    PeerUnreachable,
    Platform,
    Stolen,
    Task,
)
from ci.scheduling import Build, Stop, TaskQueue, WaitAndRetry, decide_idle, run_worker


def task(name: str) -> Task:
    return Task(
        image=name,
        dockerfile=f"{name}/Dockerfile",
        context=name,
        platform=Platform.AMD64,
        max_retries=1,
    )


# --- decide_idle: total over the steal sum ---------------------------------


def test_work_in_hand_always_wins() -> None:
    # Even with every stop condition satisfied, stolen work is built first.
    action = decide_idle(
        steal=Stolen((task("a"), task("b"))),
        peers_drained=True,
        idle_elapsed_seconds=10_000.0,
        grace_seconds=1.0,
    )
    assert action == Build(task=task("a"), deferred=(task("b"),))


def test_stops_when_every_peer_is_confirmed_drained() -> None:
    action = decide_idle(
        steal=PeerEmpty(), peers_drained=True, idle_elapsed_seconds=0.0, grace_seconds=90.0
    )
    assert isinstance(action, Stop)


def test_unreachable_peer_is_never_mistaken_for_completion() -> None:
    # The property the whole design rests on. peers_drained is False because a
    # peer could not be reached, so the slot waits rather than concluding the
    # run is over -- a late-booting peer still holds tasks.
    action = decide_idle(
        steal=PeerUnreachable("connection refused"),
        peers_drained=False,
        idle_elapsed_seconds=0.0,
        grace_seconds=90.0,
    )
    assert isinstance(action, WaitAndRetry)


def test_grace_period_bounds_the_wait() -> None:
    # Giving up costs a missed steal, never a missed build: an unstolen task
    # stays with whoever was dealt it.
    action = decide_idle(
        steal=PeerUnreachable("timeout"),
        peers_drained=False,
        idle_elapsed_seconds=91.0,
        grace_seconds=90.0,
    )
    assert isinstance(action, Stop)


def test_waits_while_still_inside_the_grace_period() -> None:
    action = decide_idle(
        steal=PeerEmpty(), peers_drained=False, idle_elapsed_seconds=89.9, grace_seconds=90.0
    )
    assert isinstance(action, WaitAndRetry)


# --- TaskQueue -------------------------------------------------------------


def test_local_takes_the_head_and_peers_take_the_tail() -> None:
    queue = TaskQueue([task("head"), task("middle"), task("tail")])
    assert queue.take_local() == task("head")
    assert queue.release(1) == (task("tail"),)


def test_a_victim_never_strips_itself_idle() -> None:
    queue = TaskQueue([task("only")])
    assert queue.release(10) == ()
    assert len(queue) == 1


def test_release_is_bounded_by_what_is_spare() -> None:
    queue = TaskQueue([task(f"t{index}") for index in range(5)])
    assert len(queue.release(10)) == 4
    assert len(queue) == 1


def test_restore_returns_tasks_to_the_head() -> None:
    queue = TaskQueue([task("existing")])
    queue.restore([task("returned")])
    assert queue.take_local() == task("returned")


# --- run_worker interpreter ------------------------------------------------


class FakeMesh:
    """A MeshView backed by another queue, so no I/O is involved."""

    def __init__(self, victim: TaskQueue | None = None, drained_after: int = 0) -> None:
        self._victim = victim
        self._calls = 0
        self._drained_after = drained_after
        self.lock = threading.Lock()

    def attempt_steal(self):
        with self.lock:
            self._calls += 1
        if self._victim is None:
            return PeerUnreachable("no peers")
        released = self._victim.release(1)
        return Stolen(released) if released else PeerEmpty()

    def peers_drained(self) -> bool:
        with self.lock:
            return self._calls >= self._drained_after


def test_worker_drains_its_own_queue() -> None:
    queue = TaskQueue([task(f"t{index}") for index in range(6)])
    outcomes = run_worker(
        queue=queue,
        mesh=FakeMesh(drained_after=1),
        execute=lambda t: BuildSucceeded(task=t, attempts=1, duration_seconds=0.0),
        slots=3,
        sleep=lambda _: None,
    )
    assert sorted(outcome.task.image for outcome in outcomes) == [f"t{i}" for i in range(6)]


def test_worker_steals_to_correct_an_imbalanced_deal() -> None:
    # The property the random deal depends on: a 9:0 split still gets shared.
    victim = TaskQueue([task(f"v{index}") for index in range(9)])
    thief_queue = TaskQueue([])
    outcomes = run_worker(
        queue=thief_queue,
        mesh=FakeMesh(victim=victim),
        execute=lambda t: BuildSucceeded(task=t, attempts=1, duration_seconds=0.0),
        slots=2,
        sleep=lambda _: None,
        grace_seconds=0.0,
    )
    # release() always retains one, so the victim keeps exactly its last task.
    assert len(outcomes) == 8
    assert len(victim) == 1


def test_a_raising_build_does_not_kill_its_slot() -> None:
    queue = TaskQueue([task("poison"), task("fine")])
    seen: list[str] = []

    def execute(t: Task):
        if t.image == "poison":
            raise RuntimeError("builder exploded")
        seen.append(t.image)
        return BuildSucceeded(task=t, attempts=1, duration_seconds=0.0)

    outcomes = run_worker(
        queue=queue,
        mesh=FakeMesh(drained_after=1),
        execute=execute,
        slots=1,
        sleep=lambda _: None,
    )
    # The slot survived the exception and went on to the next task.
    assert seen == ["fine"]
    # And the exception was recorded rather than swallowed.
    assert len(outcomes) == 2
    failed = [o for o in outcomes if isinstance(o, BuildFailed)]
    assert len(failed) == 1
    assert failed[0].task.image == "poison"
    assert "builder exploded" in failed[0].error


def test_a_failure_affecting_every_task_cannot_exit_green() -> None:
    """Guards a regression: swallowing exceptions produced an empty result set.

    A missing buildx plugin or an unreachable Docker daemon makes every task
    raise. Recording nothing would report "0 succeeded, 0 failed" and exit 0
    while having built nothing.
    """
    tasks = [task(f"t{index}") for index in range(3)]

    def always_raises(_t: Task):
        raise FileNotFoundError("docker: command not found")

    outcomes = run_worker(
        queue=TaskQueue(tasks),
        mesh=FakeMesh(drained_after=1),
        execute=always_raises,
        slots=2,
        sleep=lambda _: None,
    )

    assert len(outcomes) == len(tasks)
    assert all(isinstance(outcome, BuildFailed) for outcome in outcomes)


def test_worker_terminates_with_no_peers_and_no_work() -> None:
    outcomes = run_worker(
        queue=TaskQueue([]),
        mesh=FakeMesh(),
        execute=lambda t: BuildSucceeded(task=t, attempts=1, duration_seconds=0.0),
        slots=2,
        sleep=lambda _: None,
        grace_seconds=0.0,
    )
    assert outcomes == ()
