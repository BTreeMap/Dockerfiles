"""Work-stealing scheduler: a pure decision core inside a threaded shell.

The idle path used to be an imperative tangle of a nullable timestamp, a boolean
steal result, and two early returns, which made its most important property --
that a worker never mistakes an incomplete view of the mesh for the work being
finished -- impossible to test without running real servers. That property now
lives in `decide_idle`, a total function over a closed sum with no I/O in it.

`run_worker` is the effect interpreter: it observes the world, asks
`decide_idle` what to do, and performs it. Draining the queue is genuinely a
state machine over a shared mutable resource, not collection algebra, so it is
not forced into a pipeline shape.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, assert_never, runtime_checkable

from ci.domain import (
    BuildFailed,
    BuildOutcome,
    PeerEmpty,
    PeerUnreachable,
    StealOutcome,
    Stolen,
    Task,
)

logger = logging.getLogger("ci.scheduling")


# --- slot actions ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Build:
    """Run `task`; `deferred` are surplus stolen tasks to hand to the local queue."""

    task: Task
    deferred: tuple[Task, ...] = ()


@dataclass(frozen=True, slots=True)
class WaitAndRetry:
    """No work visible, but the view may be incomplete. Poll again."""


@dataclass(frozen=True, slots=True)
class Stop:
    reason: str


SlotAction = Build | WaitAndRetry | Stop


def decide_idle(
    steal: StealOutcome,
    peers_drained: bool,
    idle_elapsed_seconds: float,
    grace_seconds: float,
) -> SlotAction:
    """Decides what an idle slot should do next. Pure and total.

    The ordering encodes the safety argument:

    1. Work in hand always wins.
    2. Stopping requires positive evidence -- `peers_drained` is true only when
       every expected peer has been accounted for and none of them holds work it
       would ever hand over. A peer nobody has managed to contact yet leaves it
       false, so silence from a mesh still assembling itself is never read as
       completion.
    3. Otherwise the grace period bounds how long a slot waits on a peer that
       may still be booting. Expiring it costs a missed steal, never a missed
       build: an unstolen task stays with whoever was dealt it.

    Rule 3 is the fallback, not the normal path. It fires when some peer never
    answered at all; a mesh whose members all answered settles under rule 2 as
    soon as the last of them runs out of spare work.
    """
    match steal:
        case Stolen(tasks):
            return Build(task=tasks[0], deferred=tasks[1:])
        case PeerEmpty() | PeerUnreachable():
            if peers_drained:
                return Stop("every peer accounted for, none with work to spare")
            if idle_elapsed_seconds > grace_seconds:
                return Stop(f"idle {idle_elapsed_seconds:.0f}s with no reachable work")
            return WaitAndRetry()
        case _:
            assert_never(steal)


# --- shared mutable queue --------------------------------------------------


class TaskQueue:
    """A worker's pending tasks: the one deliberately mutable value in the mesh.

    Local slots pop the head and peers steal from the tail. Taking from opposite
    ends keeps a thief off the entry a local slot is about to claim, and hands
    over the work least likely to be started imminently.
    """

    def __init__(self, tasks: Iterable[Task]) -> None:
        self._tasks: deque[Task] = deque(tasks)
        self._lock = threading.Lock()

    def take_local(self) -> Task | None:
        with self._lock:
            return self._tasks.popleft() if self._tasks else None

    def _spare(self) -> int:
        """All but one. The retention rule itself, stated once.

        Two consumers must agree on it exactly: `release` bounds a handover by
        it, and the health endpoint publishes it as the evidence a thief decides
        to stop on. Written twice, a victim could advertise work it would then
        refuse to part with, which is a thief polling forever.

        The caller holds the lock; `threading.Lock` is not reentrant.
        """
        return max(0, len(self._tasks) - 1)

    def spare(self) -> int:
        """How many tasks this queue would hand to a peer that asked now.

        Published rather than inferred from a refused steal, because the two are
        different facts. A steal returns nothing both when the victim is out of
        spare work and when it lost a race for the last one, and only the first
        is grounds for a thief to give up on that peer for good.
        """
        with self._lock:
            return self._spare()

    def release(self, count: int) -> tuple[Task, ...]:
        """Releases up to `count` tasks from the tail for a stealing peer."""
        with self._lock:
            return tuple(self._tasks.pop() for _ in range(min(count, self._spare())))

    def restore(self, tasks: Iterable[Task]) -> None:
        """Returns tasks to the head, so nothing is lost mid-handoff."""
        with self._lock:
            self._tasks.extendleft(tasks)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)


# --- effect interpreter ----------------------------------------------------


@runtime_checkable
class MeshView(Protocol):
    """The capability a slot needs from the mesh, narrowed to two questions.

    A Protocol rather than a base class, so the dependency is genuinely
    structural: `MeshClient` and `SoloMesh` satisfy it by shape and neither has
    to import the scheduler to say so. As a plain class this was nominal, and
    every real call site was in fact a type error -- the scheduler accepted them
    only because nothing was checking.

    Narrow on purpose. These two questions are all a slot may ask, so the
    scheduler can be exercised without tunnels, sockets, or the GitHub API.
    """

    def attempt_steal(self) -> StealOutcome: ...

    def peers_drained(self) -> bool: ...


def run_worker(
    queue: TaskQueue,
    mesh: MeshView,
    execute: Callable[[Task], BuildOutcome],
    slots: int,
    grace_seconds: float = 90.0,
    poll_seconds: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[BuildOutcome, ...]:
    """Drains the queue with `slots` concurrent builds, stealing when idle.

    Threads rather than processes: each build is a blocking subprocess call that
    releases the GIL for essentially its whole duration, and threads let the
    slots and the mesh server share one queue without a Manager proxy.

    Returns every outcome this worker produced, in completion order.
    """
    outcomes: list[BuildOutcome] = []
    outcomes_lock = threading.Lock()

    def record(outcome: BuildOutcome) -> None:
        with outcomes_lock:
            outcomes.append(outcome)

    def run_one(task: Task) -> None:
        # A task that raises outside its own retry loop must not take the slot
        # down with it: the slot has peers' stolen work still to get through,
        # and a dead slot silently reduces this worker's capacity.
        #
        # It must still be *recorded*, though. Swallowing it without an outcome
        # would let a failure that affects every task -- a missing buildx
        # plugin, an unreachable Docker daemon -- produce an empty result set
        # and a green exit, reporting "0 succeeded, 0 failed" while having built
        # nothing at all.
        started = clock()
        try:
            record(execute(task))
        except Exception as error:
            logger.exception("Unhandled error building %s", task.image)
            record(
                BuildFailed(
                    task=task,
                    attempts=0,
                    duration_seconds=clock() - started,
                    error=f"unhandled {type(error).__name__}: {error}",
                    metrics={},
                    started_at=started,
                )
            )

    def slot(index: int) -> None:
        idle_since: float | None = None

        while True:
            local = queue.take_local()
            if local is not None:
                idle_since = None
                run_one(local)
                continue

            now = clock()
            idle_since = now if idle_since is None else idle_since

            action = decide_idle(
                steal=mesh.attempt_steal(),
                peers_drained=mesh.peers_drained(),
                idle_elapsed_seconds=now - idle_since,
                grace_seconds=grace_seconds,
            )

            match action:
                case Build(task, deferred):
                    queue.restore(deferred)
                    idle_since = None
                    run_one(task)
                case WaitAndRetry():
                    sleep(poll_seconds)
                case Stop(reason):
                    logger.info("Slot %d stopping: %s", index, reason)
                    return
                case _:
                    assert_never(action)

    threads = tuple(
        threading.Thread(target=slot, args=(index,), name=f"slot-{index}", daemon=False)
        for index in range(slots)
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    return tuple(outcomes)
