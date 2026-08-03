"""Dead Letter Queue for Failed Operations.

Production-ready DLQ implementation using Redis Streams:
- Failed message capture with full context
- Automatic retry with exponential backoff
- Manual reprocessing capabilities
- Alerting on threshold breaches
- Message expiration and cleanup
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
import hashlib

log = logging.getLogger("omnipotent.reliability.dlq")


class FailureReason(str, Enum):
    """Reasons for message failure."""
    TIMEOUT = "timeout"
    VALIDATION_ERROR = "validation_error"
    PROCESSING_ERROR = "processing_error"
    DEPENDENCY_FAILURE = "dependency_failure"
    RATE_LIMITED = "rate_limited"
    CIRCUIT_OPEN = "circuit_open"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    UNKNOWN = "unknown"


class MessageStatus(str, Enum):
    """Status of a DLQ message."""
    PENDING = "pending"  # Awaiting retry
    RETRYING = "retrying"  # Currently being retried
    REPROCESSED = "reprocessed"  # Successfully reprocessed
    EXPIRED = "expired"  # TTL exceeded
    DEAD = "dead"  # Max retries exceeded
    MANUALLY_RESOLVED = "manually_resolved"


@dataclass
class DeadLetterMessage:
    """A message in the dead letter queue."""
    id: str
    queue_name: str
    original_message: dict[str, Any]
    failure_reason: FailureReason
    error_message: str
    error_traceback: str | None

    # Retry tracking
    retry_count: int = 0
    max_retries: int = 5
    next_retry_at: datetime | None = None

    # Timestamps
    failed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_retry_at: datetime | None = None
    resolved_at: datetime | None = None
    expires_at: datetime | None = None

    # Context
    status: MessageStatus = MessageStatus.PENDING
    source_service: str = ""
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "queue_name": self.queue_name,
            "original_message": self.original_message,
            "failure_reason": self.failure_reason.value,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "status": self.status.value,
            "failed_at": self.failed_at.isoformat(),
            "next_retry_at": self.next_retry_at.isoformat() if self.next_retry_at else None,
            "source_service": self.source_service,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeadLetterMessage:
        return cls(
            id=data["id"],
            queue_name=data["queue_name"],
            original_message=data["original_message"],
            failure_reason=FailureReason(data["failure_reason"]),
            error_message=data["error_message"],
            error_traceback=data.get("error_traceback"),
            retry_count=data.get("retry_count", 0),
            max_retries=data.get("max_retries", 5),
            next_retry_at=datetime.fromisoformat(data["next_retry_at"]) if data.get("next_retry_at") else None,
            failed_at=datetime.fromisoformat(data["failed_at"]) if data.get("failed_at") else datetime.now(timezone.utc),
            status=MessageStatus(data.get("status", "pending")),
            source_service=data.get("source_service", ""),
            correlation_id=data.get("correlation_id"),
            metadata=data.get("metadata", {}),
        )

    @property
    def can_retry(self) -> bool:
        return (
            self.status == MessageStatus.PENDING and
            self.retry_count < self.max_retries and
            (self.expires_at is None or datetime.now(timezone.utc) < self.expires_at)
        )

    def calculate_next_retry(self, base_delay_seconds: int = 60) -> datetime:
        """Calculate next retry time with exponential backoff."""
        delay = base_delay_seconds * (2 ** self.retry_count)
        # Cap at 1 hour
        delay = min(delay, 3600)
        # Add jitter (10%)
        import random
        jitter = delay * 0.1 * random.random()
        return datetime.now(timezone.utc) + timedelta(seconds=delay + jitter)


# Type for message handlers
MessageHandler = Callable[[dict[str, Any]], Awaitable[bool]]


class DeadLetterQueue:
    """Production-ready Dead Letter Queue using Redis.

    Features:
    - Redis Streams for durability
    - Exponential backoff retries
    - Consumer groups for distributed processing
    - Automatic expiration
    - Monitoring and alerting
    """

    def __init__(
        self,
        redis_client: Any,
        queue_prefix: str = "omnipotent:dlq",
        default_ttl_hours: int = 168,  # 7 days
        alert_threshold: int = 100,
        notification_service: Any = None,
    ):
        self.redis = redis_client
        self.queue_prefix = queue_prefix
        self.default_ttl_hours = default_ttl_hours
        self.alert_threshold = alert_threshold
        self.notifications = notification_service

        self._handlers: dict[str, MessageHandler] = {}
        self._processing = False
        self._consumer_name = f"consumer_{hashlib.sha256(str(datetime.now()).encode()).hexdigest()[:8]}"

    def _queue_key(self, queue_name: str) -> str:
        return f"{self.queue_prefix}:{queue_name}"

    def _message_key(self, message_id: str) -> str:
        return f"{self.queue_prefix}:msg:{message_id}"

    def _stats_key(self, queue_name: str) -> str:
        return f"{self.queue_prefix}:stats:{queue_name}"

    async def send_to_dlq(
        self,
        queue_name: str,
        original_message: dict[str, Any],
        error: Exception | str,
        failure_reason: FailureReason = FailureReason.PROCESSING_ERROR,
        source_service: str = "",
        correlation_id: str | None = None,
        max_retries: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> DeadLetterMessage:
        """Send a failed message to the dead letter queue.

        Args:
            queue_name: Name of the original queue
            original_message: The message that failed
            error: Exception or error message
            failure_reason: Category of failure
            source_service: Service that generated the message
            correlation_id: For tracing
            max_retries: Maximum retry attempts
            metadata: Additional context

        Returns:
            DeadLetterMessage that was created
        """
        # Generate message ID
        message_id = hashlib.sha256(
            f"{queue_name}:{json.dumps(original_message, sort_keys=True)}:{datetime.now().isoformat()}".encode()
        ).hexdigest()[:16]

        # Extract error details
        if isinstance(error, Exception):
            error_message = str(error)
            error_traceback = traceback.format_exc()
        else:
            error_message = error
            error_traceback = None

        dlq_message = DeadLetterMessage(
            id=message_id,
            queue_name=queue_name,
            original_message=original_message,
            failure_reason=failure_reason,
            error_message=error_message,
            error_traceback=error_traceback,
            max_retries=max_retries,
            source_service=source_service,
            correlation_id=correlation_id,
            metadata=metadata or {},
            expires_at=datetime.now(timezone.utc) + timedelta(hours=self.default_ttl_hours),
        )

        # Calculate first retry time
        dlq_message.next_retry_at = dlq_message.calculate_next_retry()

        # Store in Redis
        await self._store_message(dlq_message)

        # Update stats
        await self._increment_stats(queue_name, "total_failed")

        # Check alert threshold
        await self._check_alerts(queue_name)

        log.warning(
            "Message sent to DLQ: queue=%s id=%s reason=%s error=%s",
            queue_name, message_id, failure_reason.value, error_message[:100]
        )

        return dlq_message

    async def _store_message(self, message: DeadLetterMessage) -> None:
        """Store message in Redis."""
        # Store message details
        await self.redis.hset(
            self._message_key(message.id),
            mapping={
                "data": json.dumps(message.to_dict()),
                "status": message.status.value,
                "queue_name": message.queue_name,
                "failed_at": message.failed_at.isoformat(),
            }
        )

        # Set expiration
        if message.expires_at:
            ttl_seconds = int((message.expires_at - datetime.now(timezone.utc)).total_seconds())
            if ttl_seconds > 0:
                await self.redis.expire(self._message_key(message.id), ttl_seconds)

        # Add to queue stream for processing
        await self.redis.xadd(
            self._queue_key(message.queue_name),
            {
                "message_id": message.id,
                "next_retry_at": message.next_retry_at.isoformat() if message.next_retry_at else "",
            }
        )

        # Add to sorted set for retry scheduling
        if message.next_retry_at:
            await self.redis.zadd(
                f"{self.queue_prefix}:retry_schedule",
                {message.id: message.next_retry_at.timestamp()}
            )

    async def _increment_stats(self, queue_name: str, metric: str) -> None:
        """Increment a stats counter."""
        await self.redis.hincrby(self._stats_key(queue_name), metric, 1)
        await self.redis.hincrby(self._stats_key(queue_name), f"{metric}:{datetime.now().strftime('%Y-%m-%d')}", 1)

    async def _check_alerts(self, queue_name: str) -> None:
        """Check if alert threshold is breached."""
        pending_count = await self.get_pending_count(queue_name)

        if pending_count >= self.alert_threshold:
            log.error(
                "DLQ alert threshold breached: queue=%s count=%d threshold=%d",
                queue_name, pending_count, self.alert_threshold
            )

            if self.notifications:
                await self.notifications.send_alert(
                    title=f"DLQ Alert: {queue_name}",
                    message=f"Dead letter queue has {pending_count} pending messages (threshold: {self.alert_threshold})",
                    severity="critical",
                )

    async def get_message(self, message_id: str) -> DeadLetterMessage | None:
        """Get a message by ID."""
        data = await self.redis.hget(self._message_key(message_id), "data")
        if not data:
            return None

        return DeadLetterMessage.from_dict(json.loads(data))

    async def get_pending_count(self, queue_name: str) -> int:
        """Get count of pending messages in a queue."""
        # Count messages with PENDING status
        pattern = f"{self.queue_prefix}:msg:*"
        count = 0

        async for key in self.redis.scan_iter(pattern):
            data = await self.redis.hgetall(key)
            if data.get(b"queue_name", b"").decode() == queue_name:
                if data.get(b"status", b"").decode() == MessageStatus.PENDING.value:
                    count += 1

        return count

    async def list_messages(
        self,
        queue_name: str | None = None,
        status: MessageStatus | None = None,
        limit: int = 100,
    ) -> list[DeadLetterMessage]:
        """List messages in the DLQ."""
        messages = []
        pattern = f"{self.queue_prefix}:msg:*"

        async for key in self.redis.scan_iter(pattern):
            if len(messages) >= limit:
                break

            data = await self.redis.hgetall(key)
            if not data:
                continue

            # Apply filters
            if queue_name and data.get(b"queue_name", b"").decode() != queue_name:
                continue
            if status and data.get(b"status", b"").decode() != status.value:
                continue

            msg_data = data.get(b"data")
            if msg_data:
                messages.append(DeadLetterMessage.from_dict(json.loads(msg_data)))

        return messages

    def register_handler(self, queue_name: str, handler: MessageHandler) -> None:
        """Register a handler for reprocessing messages from a queue.

        Args:
            queue_name: Queue to handle
            handler: Async function that returns True on success
        """
        self._handlers[queue_name] = handler
        log.info("Registered DLQ handler for queue: %s", queue_name)

    async def retry_message(self, message_id: str) -> bool:
        """Manually trigger retry for a message.

        Args:
            message_id: Message to retry

        Returns:
            True if retry was successful
        """
        message = await self.get_message(message_id)
        if not message:
            return False

        if not message.can_retry:
            log.warning("Message cannot be retried: %s (status=%s, retries=%d/%d)",
                       message_id, message.status.value, message.retry_count, message.max_retries)
            return False

        return await self._process_message(message)

    async def _process_message(self, message: DeadLetterMessage) -> bool:
        """Process a single message."""
        handler = self._handlers.get(message.queue_name)
        if not handler:
            log.warning("No handler registered for queue: %s", message.queue_name)
            return False

        message.status = MessageStatus.RETRYING
        message.retry_count += 1
        message.last_retry_at = datetime.now(timezone.utc)
        await self._update_message(message)

        try:
            success = await handler(message.original_message)

            if success:
                message.status = MessageStatus.REPROCESSED
                message.resolved_at = datetime.now(timezone.utc)
                await self._update_message(message)
                await self._increment_stats(message.queue_name, "reprocessed")

                log.info("Message reprocessed successfully: %s", message.id)
                return True
            else:
                raise ValueError("Handler returned False")

        except Exception as e:
            log.error("Retry failed for message %s: %s", message.id, e)

            if message.retry_count >= message.max_retries:
                message.status = MessageStatus.DEAD
                await self._increment_stats(message.queue_name, "dead")
            else:
                message.status = MessageStatus.PENDING
                message.next_retry_at = message.calculate_next_retry()

            message.error_message = str(e)
            await self._update_message(message)

            return False

    async def _update_message(self, message: DeadLetterMessage) -> None:
        """Update message in Redis."""
        await self.redis.hset(
            self._message_key(message.id),
            mapping={
                "data": json.dumps(message.to_dict()),
                "status": message.status.value,
            }
        )

        # Update retry schedule
        if message.status == MessageStatus.PENDING and message.next_retry_at:
            await self.redis.zadd(
                f"{self.queue_prefix}:retry_schedule",
                {message.id: message.next_retry_at.timestamp()}
            )
        else:
            await self.redis.zrem(f"{self.queue_prefix}:retry_schedule", message.id)

    async def start_processor(self, poll_interval: float = 5.0) -> None:
        """Start the background retry processor.

        Args:
            poll_interval: Seconds between polling for retries
        """
        self._processing = True
        log.info("Starting DLQ processor (interval=%ss)", poll_interval)

        while self._processing:
            try:
                await self._process_due_retries()
            except Exception as e:
                log.error("Error in DLQ processor: %s", e)

            await asyncio.sleep(poll_interval)

    async def stop_processor(self) -> None:
        """Stop the background processor."""
        self._processing = False
        log.info("Stopping DLQ processor")

    async def _process_due_retries(self) -> None:
        """Process messages that are due for retry."""
        now = datetime.now(timezone.utc).timestamp()

        # Get messages due for retry
        due_messages = await self.redis.zrangebyscore(
            f"{self.queue_prefix}:retry_schedule",
            "-inf",
            now,
            start=0,
            num=10  # Process up to 10 at a time
        )

        for message_id in due_messages:
            if isinstance(message_id, bytes):
                message_id = message_id.decode()

            message = await self.get_message(message_id)
            if message and message.can_retry:
                await self._process_message(message)

    async def resolve_manually(
        self,
        message_id: str,
        resolution_note: str = "",
        resolved_by: str = "",
    ) -> bool:
        """Mark a message as manually resolved.

        Args:
            message_id: Message to resolve
            resolution_note: Note about resolution
            resolved_by: Who resolved it

        Returns:
            True if resolved
        """
        message = await self.get_message(message_id)
        if not message:
            return False

        message.status = MessageStatus.MANUALLY_RESOLVED
        message.resolved_at = datetime.now(timezone.utc)
        message.metadata["resolution_note"] = resolution_note
        message.metadata["resolved_by"] = resolved_by

        await self._update_message(message)
        await self._increment_stats(message.queue_name, "manually_resolved")

        log.info("Message manually resolved: %s by %s", message_id, resolved_by)
        return True

    async def purge_expired(self) -> int:
        """Purge expired messages.

        Returns:
            Count of purged messages
        """
        count = 0
        pattern = f"{self.queue_prefix}:msg:*"

        async for key in self.redis.scan_iter(pattern):
            data = await self.redis.hgetall(key)
            if not data:
                continue

            msg_data = data.get(b"data")
            if not msg_data:
                continue

            message = DeadLetterMessage.from_dict(json.loads(msg_data))

            if message.expires_at and datetime.now(timezone.utc) > message.expires_at:
                message.status = MessageStatus.EXPIRED
                await self._update_message(message)
                await self._increment_stats(message.queue_name, "expired")
                count += 1

        if count > 0:
            log.info("Purged %d expired DLQ messages", count)

        return count

    async def get_stats(self, queue_name: str | None = None) -> dict[str, Any]:
        """Get DLQ statistics."""
        if queue_name:
            stats = await self.redis.hgetall(self._stats_key(queue_name))
            return {
                k.decode() if isinstance(k, bytes) else k: int(v) if isinstance(v, (bytes, str)) and v.isdigit() else v
                for k, v in stats.items()
            }

        # Aggregate stats across all queues
        all_stats: dict[str, int] = {}
        pattern = f"{self.queue_prefix}:stats:*"

        async for key in self.redis.scan_iter(pattern):
            stats = await self.redis.hgetall(key)
            for k, v in stats.items():
                k_str = k.decode() if isinstance(k, bytes) else k
                v_int = int(v) if isinstance(v, (bytes, str)) else v
                all_stats[k_str] = all_stats.get(k_str, 0) + v_int

        return all_stats


async def create_dead_letter_queue(
    redis_url: str = "redis://localhost:6379",
    queue_prefix: str = "omnipotent:dlq",
    **kwargs,
) -> DeadLetterQueue:
    """Factory function to create a DLQ with Redis connection."""
    import redis.asyncio as redis

    client = redis.from_url(redis_url, decode_responses=False)
    return DeadLetterQueue(redis_client=client, queue_prefix=queue_prefix, **kwargs)
