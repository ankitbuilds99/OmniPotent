"""Advanced database migration utilities.

Provides:
- Pre-migration validation and safety checks
- Automatic rollback on failure
- Zero-downtime migration patterns
- Data migration with batching
- Dry-run mode for testing
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

log = logging.getLogger("devcopilot.migrations")

T = TypeVar("T")


class MigrationStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class MigrationRisk(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class MigrationLock:
    lock_id: str
    holder: str
    acquired_at: float
    expires_at: float


@dataclass
class MigrationCheckResult:
    passed: bool
    checks: dict[str, bool]
    warnings: list[str]
    blockers: list[str]


@dataclass
class MigrationStep:
    name: str
    forward_sql: str
    rollback_sql: str
    risk: MigrationRisk = MigrationRisk.LOW
    requires_lock: bool = False
    estimated_duration_seconds: int = 0
    pre_checks: list[str] = field(default_factory=list)
    post_checks: list[str] = field(default_factory=list)


@dataclass
class MigrationResult:
    success: bool
    steps_completed: int
    steps_total: int
    duration_seconds: float
    error: str | None = None
    rollback_performed: bool = False


class MigrationManager:
    """Manages database migrations with safety features."""

    def __init__(self, engine: Any, lock_timeout: int = 300):
        self._engine = engine
        self._lock_timeout = lock_timeout
        self._current_lock: MigrationLock | None = None

    async def _execute_sql(self, session: Any, sql: str) -> Any:
        from sqlalchemy import text

        result = await session.execute(text(sql))
        return result

    async def _acquire_lock(self, session: Any, migration_id: str) -> bool:
        from sqlalchemy import text

        lock_key = int(hashlib.md5(migration_id.encode()).hexdigest()[:8], 16)

        try:
            result = await session.execute(
                text(f"SELECT pg_try_advisory_lock({lock_key})")
            )
            acquired = result.scalar()

            if acquired:
                self._current_lock = MigrationLock(
                    lock_id=str(lock_key),
                    holder=migration_id,
                    acquired_at=time.time(),
                    expires_at=time.time() + self._lock_timeout,
                )
                log.info("acquired migration lock: %s", migration_id)
                return True

            log.warning("failed to acquire migration lock: %s", migration_id)
            return False
        except Exception as e:
            log.error("lock acquisition error: %s", e)
            return False

    async def _release_lock(self, session: Any, migration_id: str) -> None:
        from sqlalchemy import text

        if self._current_lock is None:
            return

        lock_key = int(hashlib.md5(migration_id.encode()).hexdigest()[:8], 16)

        try:
            await session.execute(text(f"SELECT pg_advisory_unlock({lock_key})"))
            self._current_lock = None
            log.info("released migration lock: %s", migration_id)
        except Exception as e:
            log.error("lock release error: %s", e)

    async def run_pre_checks(
        self,
        session: Any,
        steps: list[MigrationStep],
    ) -> MigrationCheckResult:
        from sqlalchemy import text

        checks: dict[str, bool] = {}
        warnings: list[str] = []
        blockers: list[str] = []

        active_connections = await session.execute(
            text("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
        )
        conn_count = active_connections.scalar() or 0
        checks["low_connection_count"] = conn_count < 100
        if conn_count >= 100:
            warnings.append(f"High active connections: {conn_count}")

        replication_lag = await session.execute(
            text("""
                SELECT EXTRACT(EPOCH FROM (now() - pg_last_xact_replay_timestamp()))
                WHERE pg_is_in_recovery()
            """)
        )
        lag = replication_lag.scalar()
        if lag is not None:
            checks["low_replication_lag"] = lag < 10
            if lag >= 10:
                blockers.append(f"High replication lag: {lag}s")
        else:
            checks["low_replication_lag"] = True

        long_transactions = await session.execute(
            text("""
                SELECT count(*) FROM pg_stat_activity
                WHERE state = 'active'
                AND now() - xact_start > interval '5 minutes'
            """)
        )
        long_tx_count = long_transactions.scalar() or 0
        checks["no_long_transactions"] = long_tx_count == 0
        if long_tx_count > 0:
            warnings.append(f"Long-running transactions: {long_tx_count}")

        for step in steps:
            if step.risk == MigrationRisk.CRITICAL:
                checks["no_critical_without_maintenance"] = False
                blockers.append(
                    f"Critical migration '{step.name}' requires maintenance window"
                )

            for check_sql in step.pre_checks:
                try:
                    result = await session.execute(text(check_sql))
                    check_passed = result.scalar()
                    checks[f"pre_check_{step.name}"] = bool(check_passed)
                    if not check_passed:
                        blockers.append(f"Pre-check failed for {step.name}")
                except Exception as e:
                    checks[f"pre_check_{step.name}"] = False
                    blockers.append(f"Pre-check error for {step.name}: {e}")

        return MigrationCheckResult(
            passed=len(blockers) == 0,
            checks=checks,
            warnings=warnings,
            blockers=blockers,
        )

    async def migrate(
        self,
        steps: list[MigrationStep],
        migration_id: str,
        dry_run: bool = False,
        auto_rollback: bool = True,
    ) -> MigrationResult:
        from sqlalchemy.ext.asyncio import AsyncSession

        start_time = time.time()
        completed_steps: list[MigrationStep] = []

        async with self._engine.begin() as conn:
            session = AsyncSession(bind=conn)

            try:
                check_result = await self.run_pre_checks(session, steps)
                if not check_result.passed:
                    return MigrationResult(
                        success=False,
                        steps_completed=0,
                        steps_total=len(steps),
                        duration_seconds=time.time() - start_time,
                        error=f"Pre-checks failed: {check_result.blockers}",
                    )

                if any(step.requires_lock for step in steps):
                    if not await self._acquire_lock(session, migration_id):
                        return MigrationResult(
                            success=False,
                            steps_completed=0,
                            steps_total=len(steps),
                            duration_seconds=time.time() - start_time,
                            error="Could not acquire migration lock",
                        )

                for step in steps:
                    log.info("executing migration step: %s", step.name)

                    if dry_run:
                        log.info("[DRY RUN] would execute: %s", step.forward_sql[:100])
                        completed_steps.append(step)
                        continue

                    try:
                        await self._execute_sql(session, step.forward_sql)
                        completed_steps.append(step)
                        log.info("completed migration step: %s", step.name)

                        from sqlalchemy import text

                        for post_check in step.post_checks:
                            result = await session.execute(text(post_check))
                            if not result.scalar():
                                raise ValueError(f"Post-check failed for {step.name}")

                    except Exception as e:
                        log.error("migration step failed: %s - %s", step.name, e)

                        if auto_rollback:
                            await self._rollback_steps(session, completed_steps)
                            return MigrationResult(
                                success=False,
                                steps_completed=len(completed_steps),
                                steps_total=len(steps),
                                duration_seconds=time.time() - start_time,
                                error=str(e),
                                rollback_performed=True,
                            )
                        raise

                if not dry_run:
                    await conn.commit()

                return MigrationResult(
                    success=True,
                    steps_completed=len(completed_steps),
                    steps_total=len(steps),
                    duration_seconds=time.time() - start_time,
                )

            finally:
                if any(step.requires_lock for step in steps):
                    await self._release_lock(session, migration_id)

    async def _rollback_steps(
        self,
        session: Any,
        steps: list[MigrationStep],
    ) -> None:
        for step in reversed(steps):
            if not step.rollback_sql:
                log.warning("no rollback SQL for step: %s", step.name)
                continue

            try:
                log.info("rolling back step: %s", step.name)
                await self._execute_sql(session, step.rollback_sql)
                log.info("rolled back step: %s", step.name)
            except Exception as e:
                log.error("rollback failed for step %s: %s", step.name, e)

    async def rollback(
        self,
        steps: list[MigrationStep],
        migration_id: str,
    ) -> MigrationResult:
        from sqlalchemy.ext.asyncio import AsyncSession

        start_time = time.time()
        rolled_back = 0

        async with self._engine.begin() as conn:
            session = AsyncSession(bind=conn)

            try:
                for step in reversed(steps):
                    if not step.rollback_sql:
                        continue

                    log.info("rolling back: %s", step.name)
                    await self._execute_sql(session, step.rollback_sql)
                    rolled_back += 1

                await conn.commit()

                return MigrationResult(
                    success=True,
                    steps_completed=rolled_back,
                    steps_total=len(steps),
                    duration_seconds=time.time() - start_time,
                    rollback_performed=True,
                )

            except Exception as e:
                return MigrationResult(
                    success=False,
                    steps_completed=rolled_back,
                    steps_total=len(steps),
                    duration_seconds=time.time() - start_time,
                    error=str(e),
                    rollback_performed=True,
                )


class BatchDataMigrator:
    """Migrate large datasets in batches to avoid locking."""

    def __init__(
        self,
        engine: Any,
        batch_size: int = 1000,
        sleep_between_batches: float = 0.1,
    ):
        self._engine = engine
        self._batch_size = batch_size
        self._sleep = sleep_between_batches

    async def migrate_in_batches(
        self,
        select_sql: str,
        update_sql: str,
        id_column: str = "id",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> int:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import AsyncSession

        total_migrated = 0
        last_id = 0

        async with self._engine.begin() as conn:
            session = AsyncSession(bind=conn)

            while True:
                batch_select = f"""
                    {select_sql}
                    WHERE {id_column} > {last_id}
                    ORDER BY {id_column}
                    LIMIT {self._batch_size}
                """

                result = await session.execute(text(batch_select))
                rows = result.fetchall()

                if not rows:
                    break

                ids = [row[0] for row in rows]
                last_id = ids[-1]

                batch_update = f"""
                    {update_sql}
                    WHERE {id_column} IN ({','.join(str(i) for i in ids)})
                """

                await session.execute(text(batch_update))
                await session.commit()

                total_migrated += len(rows)

                if progress_callback:
                    progress_callback(total_migrated, len(rows))

                await asyncio.sleep(self._sleep)

        return total_migrated


ZERO_DOWNTIME_PATTERNS = {
    "add_column": """
-- Step 1: Add column as nullable (no lock)
ALTER TABLE {table} ADD COLUMN {column} {type};

-- Step 2: Backfill in batches (application code)

-- Step 3: Add NOT NULL constraint with validation
ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL;
""",
    "add_index": """
-- Use CONCURRENTLY to avoid blocking writes
CREATE INDEX CONCURRENTLY {index_name} ON {table} ({columns});
""",
    "rename_column": """
-- Step 1: Add new column
ALTER TABLE {table} ADD COLUMN {new_column} {type};

-- Step 2: Copy data in batches

-- Step 3: Update application to write to both columns

-- Step 4: Stop writing to old column

-- Step 5: Drop old column
ALTER TABLE {table} DROP COLUMN {old_column};
""",
    "change_type": """
-- Step 1: Add new column with new type
ALTER TABLE {table} ADD COLUMN {column}_new {new_type};

-- Step 2: Backfill with type conversion

-- Step 3: Swap columns
ALTER TABLE {table} DROP COLUMN {column};
ALTER TABLE {table} RENAME COLUMN {column}_new TO {column};
""",
}


def generate_migration_steps(
    pattern: str,
    **kwargs: Any,
) -> list[MigrationStep]:
    """Generate migration steps from a pattern."""
    template = ZERO_DOWNTIME_PATTERNS.get(pattern)
    if not template:
        raise ValueError(f"Unknown migration pattern: {pattern}")

    sql = template.format(**kwargs)

    steps = []
    for i, statement in enumerate(sql.strip().split("\n\n")):
        if statement.startswith("--"):
            continue
        steps.append(
            MigrationStep(
                name=f"{pattern}_step_{i + 1}",
                forward_sql=statement.strip(),
                rollback_sql="",
                risk=MigrationRisk.LOW,
            )
        )

    return steps
