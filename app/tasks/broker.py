"""Dramatiq broker configuration.

Supports:
- Redis broker (production)
- Stub broker (testing)
- In-memory broker (development)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    Callbacks,
    Pipelines,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)

log = logging.getLogger("devcopilot.tasks")

_broker: dramatiq.Broker | None = None


def _create_middleware() -> list[dramatiq.Middleware]:
    """Create standard middleware stack."""
    return [
        AgeLimit(),
        TimeLimit(),
        ShutdownNotifications(),
        Callbacks(),
        Pipelines(),
        Retries(
            max_retries=3,
            min_backoff=1000,
            max_backoff=60000,
        ),
    ]


def configure_broker(redis_url: str | None = None) -> dramatiq.Broker:
    """Configure the Dramatiq broker.

    Args:
        redis_url: Redis connection URL. If None, uses COPILOT_REDIS_URL
                   env var. Falls back to stub broker for testing.

    Returns:
        Configured broker instance.
    """
    global _broker

    if _broker is not None:
        return _broker

    url = redis_url or os.environ.get("COPILOT_REDIS_URL", "")

    if os.environ.get("DRAMATIQ_TEST_BROKER"):
        log.info("Using stub broker for testing")
        _broker = StubBroker()
        _broker.emit_after("process_boot")
    elif url:
        log.info("Connecting to Redis broker: %s", url.split("@")[-1])
        _broker = RedisBroker(
            url=url,
            middleware=_create_middleware(),
        )
    else:
        log.warning("No Redis URL configured, using stub broker (tasks won't persist)")
        _broker = StubBroker()
        _broker.emit_after("process_boot")

    dramatiq.set_broker(_broker)
    return _broker


def get_broker() -> dramatiq.Broker:
    """Get the configured broker, creating if needed."""
    global _broker
    if _broker is None:
        configure_broker()
    return _broker  # type: ignore


def enqueue(
    actor_name: str,
    *args: Any,
    delay: int | None = None,
    **kwargs: Any,
) -> dramatiq.Message:
    """Enqueue a task by actor name.

    Args:
        actor_name: Name of the registered actor/task.
        *args: Positional arguments for the task.
        delay: Optional delay in milliseconds before execution.
        **kwargs: Keyword arguments for the task.

    Returns:
        The enqueued message.
    """
    broker = get_broker()
    actor = broker.get_actor(actor_name)

    options: dict[str, Any] = {}
    if delay:
        options["delay"] = delay

    return actor.send_with_options(args=args, kwargs=kwargs, **options)
