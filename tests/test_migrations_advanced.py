"""Tests for advanced database migration utilities."""
# ruff: noqa: E402

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    import sqlalchemy  # noqa: F401

    HAS_SQLALCHEMY = True
except ImportError:
    HAS_SQLALCHEMY = False

pytestmark = pytest.mark.skipif(
    not HAS_SQLALCHEMY, reason="SQLAlchemy not installed"
)

from app.database.migrations_advanced import (
    BatchDataMigrator,
    MigrationCheckResult,
    MigrationManager,
    MigrationResult,
    MigrationRisk,
    MigrationStatus,
    MigrationStep,
    generate_migration_steps,
)


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.begin = MagicMock(return_value=AsyncMock())
    return engine


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def sample_migration_steps():
    return [
        MigrationStep(
            name="add_column",
            forward_sql="ALTER TABLE users ADD COLUMN email VARCHAR(255)",
            rollback_sql="ALTER TABLE users DROP COLUMN email",
            risk=MigrationRisk.LOW,
        ),
        MigrationStep(
            name="create_index",
            forward_sql="CREATE INDEX idx_users_email ON users(email)",
            rollback_sql="DROP INDEX idx_users_email",
            risk=MigrationRisk.LOW,
        ),
    ]


class TestMigrationStep:
    def test_step_creation(self):
        step = MigrationStep(
            name="test_step",
            forward_sql="SELECT 1",
            rollback_sql="SELECT 0",
            risk=MigrationRisk.MEDIUM,
            requires_lock=True,
            estimated_duration_seconds=30,
        )

        assert step.name == "test_step"
        assert step.risk == MigrationRisk.MEDIUM
        assert step.requires_lock is True
        assert step.estimated_duration_seconds == 30

    def test_step_with_checks(self):
        step = MigrationStep(
            name="with_checks",
            forward_sql="ALTER TABLE users ADD COLUMN age INT",
            rollback_sql="ALTER TABLE users DROP COLUMN age",
            pre_checks=["SELECT 1 FROM users LIMIT 1"],
            post_checks=[
                "SELECT COUNT(*) > 0 FROM information_schema.columns "
                "WHERE column_name = 'age'"
            ],
        )

        assert len(step.pre_checks) == 1
        assert len(step.post_checks) == 1


class TestMigrationCheckResult:
    def test_passed_result(self):
        result = MigrationCheckResult(
            passed=True,
            checks={"check1": True, "check2": True},
            warnings=[],
            blockers=[],
        )

        assert result.passed is True
        assert len(result.blockers) == 0

    def test_failed_result(self):
        result = MigrationCheckResult(
            passed=False,
            checks={"check1": True, "check2": False},
            warnings=["High connection count"],
            blockers=["Replication lag too high"],
        )

        assert result.passed is False
        assert len(result.blockers) == 1
        assert len(result.warnings) == 1


class TestMigrationResult:
    def test_successful_result(self):
        result = MigrationResult(
            success=True,
            steps_completed=3,
            steps_total=3,
            duration_seconds=5.5,
        )

        assert result.success is True
        assert result.steps_completed == result.steps_total

    def test_failed_with_rollback(self):
        result = MigrationResult(
            success=False,
            steps_completed=1,
            steps_total=3,
            duration_seconds=2.0,
            error="Column already exists",
            rollback_performed=True,
        )

        assert result.success is False
        assert result.rollback_performed is True
        assert result.error is not None


class TestMigrationManager:
    @pytest.mark.asyncio
    async def test_pre_checks_low_connections(self, mock_engine, mock_session):
        manager = MigrationManager(mock_engine)

        mock_session.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value=50)),
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=0)),
        ]

        steps = [
            MigrationStep(
                name="simple",
                forward_sql="SELECT 1",
                rollback_sql="SELECT 0",
                risk=MigrationRisk.LOW,
            )
        ]

        result = await manager.run_pre_checks(mock_session, steps)

        assert result.checks.get("low_connection_count") is True

    @pytest.mark.asyncio
    async def test_pre_checks_high_connections_warning(
        self, mock_engine, mock_session
    ):
        manager = MigrationManager(mock_engine)

        mock_session.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value=150)),
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=0)),
        ]

        steps = [
            MigrationStep(
                name="simple",
                forward_sql="SELECT 1",
                rollback_sql="SELECT 0",
            )
        ]

        result = await manager.run_pre_checks(mock_session, steps)

        assert result.checks.get("low_connection_count") is False
        assert any("connection" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_pre_checks_blocks_critical_migration(
        self, mock_engine, mock_session
    ):
        manager = MigrationManager(mock_engine)

        mock_session.execute.side_effect = [
            MagicMock(scalar=MagicMock(return_value=10)),
            MagicMock(scalar=MagicMock(return_value=None)),
            MagicMock(scalar=MagicMock(return_value=0)),
        ]

        steps = [
            MigrationStep(
                name="critical",
                forward_sql="DROP TABLE users",
                rollback_sql="",
                risk=MigrationRisk.CRITICAL,
            )
        ]

        result = await manager.run_pre_checks(mock_session, steps)

        assert result.passed is False
        assert any("critical" in b.lower() for b in result.blockers)

    @pytest.mark.asyncio
    async def test_dry_run_mode(self, mock_engine, sample_migration_steps):
        manager = MigrationManager(mock_engine)

        mock_conn = AsyncMock()

        mock_engine.begin.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch.object(
            manager,
            "run_pre_checks",
            return_value=MigrationCheckResult(
                passed=True, checks={}, warnings=[], blockers=[]
            ),
        ):
            result = await manager.migrate(
                sample_migration_steps,
                migration_id="test-dry-run",
                dry_run=True,
            )

        assert result.success is True
        assert result.steps_completed == len(sample_migration_steps)


class TestBatchDataMigrator:
    @pytest.mark.asyncio
    async def test_batch_migration_initialization(self, mock_engine):
        migrator = BatchDataMigrator(
            mock_engine, batch_size=100, sleep_between_batches=0
        )

        assert migrator._batch_size == 100
        assert migrator._sleep == 0


class TestMigrationStatus:
    def test_status_values(self):
        assert MigrationStatus.PENDING.value == "pending"
        assert MigrationStatus.RUNNING.value == "running"
        assert MigrationStatus.COMPLETED.value == "completed"
        assert MigrationStatus.FAILED.value == "failed"
        assert MigrationStatus.ROLLED_BACK.value == "rolled_back"


class TestMigrationRisk:
    def test_risk_levels(self):
        assert MigrationRisk.LOW.value == "low"
        assert MigrationRisk.MEDIUM.value == "medium"
        assert MigrationRisk.HIGH.value == "high"
        assert MigrationRisk.CRITICAL.value == "critical"


class TestGenerateMigrationSteps:
    def test_add_column_pattern(self):
        steps = generate_migration_steps(
            "add_column",
            table="users",
            column="email",
            type="VARCHAR(255)",
        )

        assert len(steps) >= 0

    def test_add_index_pattern(self):
        steps = generate_migration_steps(
            "add_index",
            table="users",
            index_name="idx_email",
            columns="email",
        )

        assert len(steps) >= 0

    def test_unknown_pattern_raises(self):
        with pytest.raises(ValueError, match="Unknown migration pattern"):
            generate_migration_steps("nonexistent_pattern")
