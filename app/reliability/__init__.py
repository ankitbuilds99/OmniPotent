"""Reliability Layer.

Production-ready reliability components:
- Dead Letter Queue with Redis Streams
- Circuit breakers
- Retries with backoff
- Fallback handling
- Checkpointing
"""

from app.reliability.dead_letter_queue import (
    DeadLetterQueue,
    DeadLetterMessage,
    FailureReason,
    MessageStatus,
    create_dead_letter_queue,
)

__all__ = [
    "DeadLetterQueue",
    "DeadLetterMessage",
    "FailureReason",
    "MessageStatus",
    "create_dead_letter_queue",
]
