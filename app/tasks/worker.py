"""Dramatiq worker configuration and runner.

Provides a standalone worker process for background tasks.
Can be run with: python -m app.tasks.worker
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from typing import Any

log = logging.getLogger("omnipotent.tasks.worker")


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


def run_scheduler() -> None:
    """Run the APScheduler for periodic tasks.

    Schedules:
    - Alert evaluation every 60 seconds
    - Session cleanup every hour
    - Usage sync every 5 minutes
    """
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger

    from app.tasks.tasks import (
        cleanup_expired_sessions,
        process_alert_evaluation,
    )

    scheduler = BlockingScheduler()

    scheduler.add_job(
        lambda: process_alert_evaluation.send([]),
        IntervalTrigger(seconds=60),
        id="alert_evaluation",
        name="Evaluate alert rules",
    )

    scheduler.add_job(
        lambda: cleanup_expired_sessions.send(24),
        IntervalTrigger(hours=1),
        id="session_cleanup",
        name="Clean up expired sessions",
    )

    def shutdown(signum: int, frame: Any) -> None:
        log.info("Scheduler shutdown requested")
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("Starting task scheduler")
    scheduler.start()


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
        run_scheduler()
    elif args.mode == "all":
        import multiprocessing

        scheduler_proc = multiprocessing.Process(target=run_scheduler)
        scheduler_proc.start()
        start_worker(args.queues, args.processes, args.threads)
        scheduler_proc.join()
    else:
        start_worker(args.queues, args.processes, args.threads)
