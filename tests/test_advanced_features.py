"""Tests for advanced platform features (Tier 1-4)."""

from datetime import datetime, timedelta, timezone


class TestAuditLogging:
    """Tests for audit logging system."""

    def test_audit_entry_creation(self):
        """Test creating audit entries."""
        from app.audit.logger import log_action, AuditAction

        entry = log_action(
            action=AuditAction.INVESTIGATION_CREATE,
            actor_id="user_audit_1",
            org_id="org_audit_test",
            resource_type="investigation",
            resource_id="inv_123",
            description="Created new investigation",
        )

        assert entry.action == AuditAction.INVESTIGATION_CREATE
        assert entry.actor_id == "user_audit_1"

    def test_auth_event_logging(self):
        """Test logging auth events."""
        from app.audit.logger import log_auth_event, AuditAction, AuditSeverity

        entry = log_auth_event(
            action=AuditAction.LOGIN,
            actor_id="user_auth_1",
            success=True,
            actor_ip="192.168.1.1",
        )

        assert entry.severity == AuditSeverity.INFO

        failed = log_auth_event(
            action=AuditAction.LOGIN_FAILED,
            actor_id="user_auth_1",
            success=False,
            error_message="Invalid credentials",
        )

        assert failed.severity == AuditSeverity.WARNING

    def test_config_change_logging(self):
        """Test logging config changes."""
        from app.audit.logger import log_config_change

        entry = log_config_change(
            actor_id="admin_1",
            setting_name="max_concurrent_investigations",
            old_value=5,
            new_value=10,
            org_id="org_config_test",
        )

        assert entry.old_value == {"value": 5}
        assert entry.new_value == {"value": 10}

    def test_audit_search(self):
        """Test searching audit logs."""
        from app.audit.logger import (
            log_action,
            search_audit_logs,
            AuditAction,
        )

        log_action(
            action=AuditAction.RUNBOOK_EXECUTE,
            actor_id="user_search_test",
            org_id="org_search_test",
        )

        results = search_audit_logs(
            org_id="org_search_test",
            actions=[AuditAction.RUNBOOK_EXECUTE],
            limit=10,
        )

        assert isinstance(results, list)

    def test_audit_summary(self):
        """Test audit summary generation."""
        from app.audit.logger import get_audit_summary

        summary = get_audit_summary("org_summary_test")

        assert "total_entries" in summary
        assert "by_action" in summary
        assert "by_category" in summary


# SLO/SLI tracking used to be tested here against `_SLOS` and `_SLI_DATA`, two
# module dicts. Those moved to the `slos` and `sli_measurements` tables and the
# tracker is async now, so the same behaviour — plus the org filter, the
# retention pass and the timestamp handling the dicts never needed — is covered
# against a real database in tests/test_slo_store.py.


class TestOnCallScheduler:
    """Tests for on-call scheduling."""

    def test_schedule_creation(self):
        """Test creating on-call schedules."""
        from app.oncall.scheduler import create_schedule

        schedule = create_schedule(
            org_id="org_oncall_test",
            name="Platform On-Call",
            description="Primary on-call rotation",
        )

        assert schedule.name == "Platform On-Call"

    def test_rotation_management(self):
        """Test adding rotations to schedules."""
        from app.oncall.scheduler import (
            OnCallScheduler,
            OnCallUser,
            RotationType,
        )

        scheduler = OnCallScheduler("org_rotation_test")
        schedule = scheduler.create_schedule("Test Schedule")

        users = [
            OnCallUser(user_id="user_1", name="Alice", email="alice@example.com"),
            OnCallUser(user_id="user_2", name="Bob", email="bob@example.com"),
        ]

        rotation = scheduler.add_rotation(
            schedule.id,
            name="Weekly Rotation",
            users=users,
            rotation_type=RotationType.WEEKLY,
        )

        assert rotation is not None
        assert len(rotation.users) == 2

    def test_current_oncall(self):
        """Test getting current on-call user."""
        from app.oncall.scheduler import (
            OnCallScheduler,
            OnCallUser,
            RotationType,
        )

        scheduler = OnCallScheduler("org_current_test")
        schedule = scheduler.create_schedule("Test Schedule")

        users = [
            OnCallUser(user_id="user_1", name="Alice", email="alice@example.com"),
        ]

        scheduler.add_rotation(
            schedule.id,
            name="Daily Rotation",
            users=users,
            rotation_type=RotationType.DAILY,
        )

        oncall = scheduler.get_current_oncall(schedule.id)
        assert oncall is not None
        assert oncall.name == "Alice"

    def test_override_creation(self):
        """Test creating on-call overrides."""
        from app.oncall.scheduler import (
            OnCallScheduler,
            OnCallUser,
            RotationType,
        )

        scheduler = OnCallScheduler("org_override_test")
        schedule = scheduler.create_schedule("Test Schedule")

        users = [
            OnCallUser(user_id="user_1", name="Alice", email="alice@example.com"),
        ]
        scheduler.add_rotation(schedule.id, "Rotation", users, RotationType.WEEKLY)

        override_user = OnCallUser(
            user_id="user_2", name="Bob", email="bob@example.com"
        )

        now = datetime.now(timezone.utc)
        override = scheduler.create_override(
            schedule.id,
            user=override_user,
            start_time=now,
            end_time=now + timedelta(hours=8),
            reason="Alice is on PTO",
        )

        assert override is not None

        current = scheduler.get_current_oncall(schedule.id)
        assert current.name == "Bob"


class TestJiraIntegration:
    """Tests for Jira integration."""

    def test_jira_config(self):
        """Test Jira configuration."""
        from app.integrations.jira import JiraConfig

        config = JiraConfig()
        assert not config.is_configured()

        config = JiraConfig(
            base_url="https://company.atlassian.net",
            email="user@company.com",
            api_token="token123",
            project_key="OPS",
        )
        assert config.is_configured()

    def test_jira_issue_dataclass(self):
        """Test JiraIssue dataclass."""
        from app.integrations.jira import JiraIssue

        issue = JiraIssue(
            key="OPS-123",
            id="12345",
            summary="Test Issue",
            description="Test description",
            status="Open",
            priority="High",
        )

        data = issue.to_dict()
        assert data["key"] == "OPS-123"
        assert data["priority"] == "High"
