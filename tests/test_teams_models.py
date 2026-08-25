"""Tests for team models."""

import pytest
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.teams.models import (
    TeamRole,
    InvitationStatus,
    Team,
    TeamMember,
    TeamInvitation,
    CreateTeamRequest,
    UpdateTeamRequest,
    InviteMemberRequest,
)


class TestTeamRole:
    """Tests for TeamRole enum."""

    def test_role_values(self):
        """All role values should be correct."""
        assert TeamRole.OWNER.value == "owner"
        assert TeamRole.ADMIN.value == "admin"
        assert TeamRole.MEMBER.value == "member"
        assert TeamRole.VIEWER.value == "viewer"

    def test_role_from_string(self):
        """Roles should be creatable from strings."""
        assert TeamRole("owner") == TeamRole.OWNER
        assert TeamRole("admin") == TeamRole.ADMIN
        assert TeamRole("member") == TeamRole.MEMBER
        assert TeamRole("viewer") == TeamRole.VIEWER


class TestInvitationStatus:
    """Tests for InvitationStatus enum."""

    def test_status_values(self):
        """All status values should be correct."""
        assert InvitationStatus.PENDING.value == "pending"
        assert InvitationStatus.ACCEPTED.value == "accepted"
        assert InvitationStatus.EXPIRED.value == "expired"
        assert InvitationStatus.CANCELLED.value == "cancelled"


class TestTeam:
    """Tests for Team model."""

    def test_team_creation(self):
        """Team should be created with defaults."""
        team = Team(name="Test Team", slug="test-team", owner_id="user123")
        assert team.name == "Test Team"
        assert team.slug == "test-team"
        assert team.owner_id == "user123"
        assert team.id is not None
        UUID(team.id)  # Should be valid UUID
        assert isinstance(team.created_at, datetime)
        assert isinstance(team.updated_at, datetime)
        assert team.settings == {}

    def test_team_with_settings(self):
        """Team should accept custom settings."""
        settings = {"theme": "dark", "notifications": True}
        team = Team(
            name="Custom Team",
            slug="custom",
            owner_id="user456",
            settings=settings,
        )
        assert team.settings == settings

    def test_team_serialization(self):
        """Team should serialize to dict."""
        team = Team(name="Test", slug="test", owner_id="user1")
        data = team.model_dump()
        assert data["name"] == "Test"
        assert data["slug"] == "test"
        assert data["owner_id"] == "user1"


class TestTeamMember:
    """Tests for TeamMember model."""

    def test_member_creation(self):
        """TeamMember should be created with defaults."""
        member = TeamMember(
            team_id="team123",
            user_id="user456",
            email="test@example.com",
        )
        assert member.team_id == "team123"
        assert member.user_id == "user456"
        assert member.email == "test@example.com"
        assert member.role == TeamRole.MEMBER
        assert member.last_active is None
        UUID(member.id)

    def test_member_with_role(self):
        """TeamMember should accept custom role."""
        member = TeamMember(
            team_id="team123",
            user_id="user456",
            email="admin@example.com",
            role=TeamRole.ADMIN,
        )
        assert member.role == TeamRole.ADMIN


class TestTeamInvitation:
    """Tests for TeamInvitation model."""

    def test_invitation_creation(self):
        """TeamInvitation should be created with defaults."""
        invite = TeamInvitation(
            team_id="team123",
            email="invite@example.com",
            invited_by="user456",
        )
        assert invite.team_id == "team123"
        assert invite.email == "invite@example.com"
        assert invite.invited_by == "user456"
        assert invite.status == InvitationStatus.PENDING
        assert invite.role == TeamRole.MEMBER
        assert invite.accepted_at is None
        UUID(invite.id)

    def test_invitation_expires_in_7_days(self):
        """Invitation should expire in 7 days by default."""
        invite = TeamInvitation(
            team_id="team123",
            email="invite@example.com",
            invited_by="user456",
        )
        # Check expires_at is approximately 7 days from now. Aware, not
        # `utcnow()`: these timestamps round trip through a
        # `DateTime(timezone=True)` column, and a naive-vs-aware subtraction is
        # a TypeError rather than a failed assertion.
        expected = datetime.now(timezone.utc) + timedelta(days=7)
        assert invite.expires_at.tzinfo is not None
        delta = abs((invite.expires_at - expected).total_seconds())
        assert delta < 60  # Within 1 minute

    def test_invitation_with_custom_role(self):
        """Invitation should accept custom role."""
        invite = TeamInvitation(
            team_id="team123",
            email="admin@example.com",
            invited_by="user456",
            role=TeamRole.ADMIN,
        )
        assert invite.role == TeamRole.ADMIN


class TestCreateTeamRequest:
    """Tests for CreateTeamRequest model."""

    def test_create_request_minimal(self):
        """CreateTeamRequest should work with just name."""
        req = CreateTeamRequest(name="My Team")
        assert req.name == "My Team"
        assert req.slug is None

    def test_create_request_with_slug(self):
        """CreateTeamRequest should accept slug."""
        req = CreateTeamRequest(name="My Team", slug="my-team")
        assert req.name == "My Team"
        assert req.slug == "my-team"

    def test_create_request_name_validation(self):
        """CreateTeamRequest should validate name length."""
        with pytest.raises(ValueError):
            CreateTeamRequest(name="")  # Too short

        # 100 chars should be fine
        req = CreateTeamRequest(name="A" * 100)
        assert len(req.name) == 100

        with pytest.raises(ValueError):
            CreateTeamRequest(name="A" * 101)  # Too long


class TestUpdateTeamRequest:
    """Tests for UpdateTeamRequest model."""

    def test_update_request_empty(self):
        """UpdateTeamRequest should work with no fields."""
        req = UpdateTeamRequest()
        assert req.name is None
        assert req.settings is None

    def test_update_request_with_name(self):
        """UpdateTeamRequest should accept name."""
        req = UpdateTeamRequest(name="New Name")
        assert req.name == "New Name"

    def test_update_request_with_settings(self):
        """UpdateTeamRequest should accept settings."""
        req = UpdateTeamRequest(settings={"key": "value"})
        assert req.settings == {"key": "value"}


class TestInviteMemberRequest:
    """Tests for InviteMemberRequest model."""

    def test_invite_request(self):
        """InviteMemberRequest should require email."""
        req = InviteMemberRequest(email="test@example.com")
        assert req.email == "test@example.com"
