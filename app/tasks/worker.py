"""Dramatiq worker configuration and runner.

Provides a standalone worker process for background tasks.
Can be run with: python -m app.tasks.worker

Which actors actually work here
------------------------------
A Dramatiq worker is a separate OS process from the API. Anything an actor needs
that lives in the API's memory is unreachable from it, and no result backend is
configured (see :mod:`app.tasks.broker`), so every actor's return value is
discarded once it finishes. That splits the seven actors three ways:

- Deployable today — ``send_notification``, ``sync_usage_to_stripe``. Both are
  fire-and-forget outbound HTTP built from env settings and their own arguments,
  which is exactly what a worker is for: retries with backoff, off the request path.
- Blocked on shared state — ``run_investigation``, ``execute_runbook_async``. Both
  do real work but hand back a result nobody can read: the investigation report and
  the runbook execution record are written into the worker's own memory, so the
  API's lookup endpoints would 404 on the IDs they return. These become deployable
  once investigation state moves to a shared store.
- Not deployable — ``process_webhook`` (a no-op that returns "processed"),
  ``cleanup_expired_sessions`` (API-process state), ``process_alert_evaluation``
  (needs the metric history that lives in the API's alert engine).

The worker still consumes every queue. Restricting it to the deployable two would
mean a message sent to another queue sat there forever with nothing to say so —
the failure modes above are handled in the actors themselves, where they can be
reported, rather than by quietly not listening.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Callable
from typing import Any, NamedTuple

log = logging.getLogger("omnipotent.tasks.worker")


class ScheduledJob(NamedTuple):
    """A periodic job for :func:`run_scheduler`."""

    id: str
    name: str
    interval_seconds: int
    send: Callable[[], Any]


# Periodic jobs, in the form the scheduler registers them.
#
# Empty on purpose. Both jobs this list used to hold were removed because neither
# could do its work from a scheduler process — see run_scheduler's docstring. The
# list is kept (rather than the scheduler deleted) because it is the seam a real
# periodic job plugs into, and because an empty list is what makes run_scheduler
# refuse to start instead of idling.
SCHEDULED_JOBS: list[ScheduledJob] = []


def start_worker(
    queues: list[str] | None = None,
    processes: int = 2,
    threads: int = 8,
) -> None:
    """Start the Dramatiq worker.

    Args:
        queues: List of queues to process. None = all queues.
        processes: Number of worker processes.
        threads: Threads per process.
    """
    from app.tasks.broker import configure_broker

    configure_broker()

    import app.tasks.tasks  # noqa: F401 - registers actors

    from dramatiq.cli import main as dramatiq_main

    args = [
        "dramatiq",
        "app.tasks.tasks",
        "--processes", str(processes),
        "--threads", str(threads),
    ]

    if queues:
        for q in queues:
            args.extend(["--queues", q])

    sys.argv = args

    try:
        dramatiq_main()
    except KeyboardInterrupt:
        log.info("Worker shutdown requested")


def run_scheduler() -> bool:
    """Run the APScheduler for periodic tasks. Returns False if it declined to start.

    There are currently no jobs to run, so this refuses to start rather than idle.
    Deploying a scheduler container that holds an empty job table would show a
    healthy, restart-free pod doing nothing, which is a worse signal than a pod
    that exits with a reason.

    Both jobs the docstring used to advertise were removed:

    - Session cleanup, hourly. Sessions live in ``app.api.main._SESSIONS``, in the
      API process; a scheduler process cannot evict them. It logged "completed" on
      every run, which reads as "cleanup ran, nothing was due" (see
      ``cleanup_expired_sessions``).
    - Alert evaluation, every 60 seconds. It was sent an empty service list, and
      ``AlertEngine.evaluate_all([])`` iterates services inside its rule loop, so it
      evaluated nothing on every tick for as long as it ran. Passing a real service
      list would be worse, not better: rules and metric history live in the API
      process's engine singleton, so a scheduler-side engine sees an empty history —
      every ABSENCE rule would fire "No metrics received" for a service that is
      fine. Alert evaluation belongs in the process being fed metrics; the engine
      already has an in-process loop for it (``AlertEngine.start``).

    Usage sync was listed in this docstring but never actually scheduled:
    ``sync_usage_to_stripe`` needs an org and a billing period, which an interval
    trigger has no way to supply. It is enqueued per-org by the billing path.
    """
    if not SCHEDULED_JOBS:
        log.error(
            "Scheduler has no jobs registered; not starting. See SCHEDULED_JOBS in "
            "%s for why the previous jobs were removed and what a new one needs.",
            __name__,
        )
        return False

    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler = BlockingScheduler()

    for job in SCHEDULED_JOBS:
        scheduler.add_job(
            job.send,
            IntervalTrigger(seconds=job.interval_seconds),
            id=job.id,
            name=job.name,
        )

    def shutdown(signum: int, frame: Any) -> None:
        log.info("Scheduler shutdown requested")
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("Starting task scheduler with %d job(s)", len(SCHEDULED_JOBS))
    scheduler.start()
    return True


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=os.environ.get("COPILOT_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="OmniPotent task worker")
    parser.add_argument(
        "--mode",
        choices=["worker", "scheduler", "all"],
        default="worker",
        help="Run mode: worker (process tasks), scheduler (periodic tasks), all (both)",
    )
    parser.add_argument(
        "--queues",
        nargs="+",
        help="Queues to process (worker mode only)",
    )
    parser.add_argument(
        "--processes",
        type=int,
        default=2,
        help="Number of worker processes",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=8,
        help="Threads per process",
    )

    args = parser.parse_args()

    if args.mode == "scheduler":
        # Exit non-zero when there is nothing to schedule, so a container in this
        # mode crash-loops visibly instead of sitting "healthy" with no work.
        sys.exit(0 if run_scheduler() else 1)
    elif args.mode == "all":
        if SCHEDULED_JOBS:
            import multiprocessing

            scheduler_proc = multiprocessing.Process(target=run_scheduler)
            scheduler_proc.start()
            start_worker(args.queues, args.processes, args.threads)
            scheduler_proc.join()
        else:
            log.warning("No scheduled jobs; running the worker only")
            start_worker(args.queues, args.processes, args.threads)
    else:
        start_worker(args.queues, args.processes, args.threads)
