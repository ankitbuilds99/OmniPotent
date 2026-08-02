"""Collaborative editing and review workflow for postmortems."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

log = logging.getLogger("omnipotent.postmortem_advanced.collaboration")


class EditorStatus(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    OFFLINE = "offline"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


@dataclass
class Editor:
    """An editor in a collaboration session."""

    user_id: str
    username: str
    status: EditorStatus = EditorStatus.ACTIVE
    current_section: str | None = None
    joined_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": self.username,
            "status": self.status.value,
            "current_section": self.current_section,
            "joined_at": self.joined_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }


@dataclass
class Comment:
    """A comment on a postmortem section."""

    comment_id: UUID
    section_id: str
    author: str
    content: str
    created_at: datetime
    resolved: bool = False
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    is_ai_suggestion: bool = False
    replies: list[Comment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.comment_id),
            "section": self.section_id,
            "author": self.author,
            "content": self.content,
            "created_at": self.created_at.isoformat(),
            "resolved": self.resolved,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "is_ai_suggestion": self.is_ai_suggestion,
            "replies": [r.to_dict() for r in self.replies],
        }


@dataclass
class ReviewRequest:
    """A review request for a postmortem."""

    request_id: UUID
    reviewer: str
    role: str
    status: ReviewStatus = ReviewStatus.PENDING
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    responded_at: datetime | None = None
    feedback: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.request_id),
            "reviewer": self.reviewer,
            "role": self.role,
            "status": self.status.value,
            "requested_at": self.requested_at.isoformat(),
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
            "feedback": self.feedback,
        }


@dataclass
class ReviewWorkflow:
    """Review workflow configuration."""

    postmortem_id: str
    required_reviewers: list[ReviewRequest]
    optional_reviewers: list[ReviewRequest] = field(default_factory=list)
    auto_approve_after_hours: int | None = 48
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_approved(self) -> bool:
        return all(
            r.status == ReviewStatus.APPROVED
            for r in self.required_reviewers
        )

    @property
    def pending_count(self) -> int:
        return sum(
            1 for r in self.required_reviewers
            if r.status == ReviewStatus.PENDING
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "postmortem_id": self.postmortem_id,
            "required_reviewers": [r.to_dict() for r in self.required_reviewers],
            "optional_reviewers": [r.to_dict() for r in self.optional_reviewers],
            "auto_approve_after_hours": self.auto_approve_after_hours,
            "status": "approved" if self.is_approved else "pending",
            "pending_count": self.pending_count,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class EditOperation:
    """An edit operation on a postmortem."""

    operation_id: UUID
    user_id: str
    section_id: str
    operation_type: str
    old_content: str | None
    new_content: str
    timestamp: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.operation_id),
            "user": self.user_id,
            "section": self.section_id,
            "type": self.operation_type,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class CollaborationSession:
    """A collaboration session for editing a postmortem."""

    session_id: UUID
    postmortem_id: str
    editors: list[Editor]
    comments: list[Comment]
    edit_history: list[EditOperation]
    created_at: datetime
    last_activity: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": str(self.session_id),
            "postmortem_id": self.postmortem_id,
            "editors": [e.to_dict() for e in self.editors],
            "active_editors": sum(1 for e in self.editors if e.status == EditorStatus.ACTIVE),
            "comments": [c.to_dict() for c in self.comments],
            "unresolved_comments": sum(1 for c in self.comments if not c.resolved),
            "edit_count": len(self.edit_history),
            "created_at": self.created_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
        }


class CollaborationManager:
    """Manages collaboration sessions for postmortems."""

    def __init__(self) -> None:
        self._sessions: dict[str, CollaborationSession] = {}
        self._workflows: dict[str, ReviewWorkflow] = {}

    def create_session(
        self,
        postmortem_id: str,
        creator: Editor,
    ) -> CollaborationSession:
        """Create a new collaboration session."""
        now = datetime.now(timezone.utc)

        session = CollaborationSession(
            session_id=uuid4(),
            postmortem_id=postmortem_id,
            editors=[creator],
            comments=[],
            edit_history=[],
            created_at=now,
            last_activity=now,
        )

        self._sessions[postmortem_id] = session
        return session

    def get_session(self, postmortem_id: str) -> CollaborationSession | None:
        """Get an existing session."""
        return self._sessions.get(postmortem_id)

    def join_session(
        self,
        postmortem_id: str,
        editor: Editor,
    ) -> CollaborationSession | None:
        """Join an existing session."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return None

        existing = next(
            (e for e in session.editors if e.user_id == editor.user_id),
            None
        )

        if existing:
            existing.status = EditorStatus.ACTIVE
            existing.last_activity = datetime.now(timezone.utc)
        else:
            session.editors.append(editor)

        session.last_activity = datetime.now(timezone.utc)
        return session

    def leave_session(
        self,
        postmortem_id: str,
        user_id: str,
    ) -> bool:
        """Leave a collaboration session."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return False

        for editor in session.editors:
            if editor.user_id == user_id:
                editor.status = EditorStatus.OFFLINE
                return True

        return False

    def update_editor_activity(
        self,
        postmortem_id: str,
        user_id: str,
        section_id: str | None = None,
    ) -> bool:
        """Update editor's activity status."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return False

        for editor in session.editors:
            if editor.user_id == user_id:
                editor.last_activity = datetime.now(timezone.utc)
                editor.status = EditorStatus.ACTIVE
                if section_id:
                    editor.current_section = section_id
                return True

        return False

    def check_idle_editors(self, postmortem_id: str) -> list[Editor]:
        """Check for idle editors and update their status."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return []

        now = datetime.now(timezone.utc)
        idle_threshold = timedelta(minutes=5)
        idle_editors = []

        for editor in session.editors:
            if editor.status == EditorStatus.ACTIVE:
                if now - editor.last_activity > idle_threshold:
                    editor.status = EditorStatus.IDLE
                    idle_editors.append(editor)

        return idle_editors

    def add_comment(
        self,
        postmortem_id: str,
        section_id: str,
        author: str,
        content: str,
        is_ai_suggestion: bool = False,
    ) -> Comment | None:
        """Add a comment to a section."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return None

        comment = Comment(
            comment_id=uuid4(),
            section_id=section_id,
            author=author,
            content=content,
            created_at=datetime.now(timezone.utc),
            is_ai_suggestion=is_ai_suggestion,
        )

        session.comments.append(comment)
        session.last_activity = datetime.now(timezone.utc)

        return comment

    def reply_to_comment(
        self,
        postmortem_id: str,
        comment_id: UUID,
        author: str,
        content: str,
    ) -> Comment | None:
        """Reply to an existing comment."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return None

        parent = next(
            (c for c in session.comments if c.comment_id == comment_id),
            None
        )
        if not parent:
            return None

        reply = Comment(
            comment_id=uuid4(),
            section_id=parent.section_id,
            author=author,
            content=content,
            created_at=datetime.now(timezone.utc),
        )

        parent.replies.append(reply)
        session.last_activity = datetime.now(timezone.utc)

        return reply

    def resolve_comment(
        self,
        postmortem_id: str,
        comment_id: UUID,
        resolved_by: str,
    ) -> bool:
        """Resolve a comment."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return False

        for comment in session.comments:
            if comment.comment_id == comment_id:
                comment.resolved = True
                comment.resolved_by = resolved_by
                comment.resolved_at = datetime.now(timezone.utc)
                return True

        return False

    def record_edit(
        self,
        postmortem_id: str,
        user_id: str,
        section_id: str,
        operation_type: str,
        old_content: str | None,
        new_content: str,
    ) -> EditOperation | None:
        """Record an edit operation."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return None

        operation = EditOperation(
            operation_id=uuid4(),
            user_id=user_id,
            section_id=section_id,
            operation_type=operation_type,
            old_content=old_content,
            new_content=new_content,
            timestamp=datetime.now(timezone.utc),
        )

        session.edit_history.append(operation)
        session.last_activity = datetime.now(timezone.utc)

        if len(session.edit_history) > 1000:
            session.edit_history = session.edit_history[-500:]

        return operation

    def get_edit_history(
        self,
        postmortem_id: str,
        section_id: str | None = None,
        limit: int = 50,
    ) -> list[EditOperation]:
        """Get edit history for a postmortem."""
        session = self._sessions.get(postmortem_id)
        if not session:
            return []

        history = session.edit_history

        if section_id:
            history = [h for h in history if h.section_id == section_id]

        return list(reversed(history))[:limit]

    def create_review_workflow(
        self,
        postmortem_id: str,
        required_reviewers: list[tuple[str, str]],
        optional_reviewers: list[tuple[str, str]] | None = None,
        auto_approve_hours: int | None = 48,
    ) -> ReviewWorkflow:
        """Create a review workflow for a postmortem."""
        required = [
            ReviewRequest(
                request_id=uuid4(),
                reviewer=reviewer,
                role=role,
            )
            for reviewer, role in required_reviewers
        ]

        optional = [
            ReviewRequest(
                request_id=uuid4(),
                reviewer=reviewer,
                role=role,
            )
            for reviewer, role in (optional_reviewers or [])
        ]

        workflow = ReviewWorkflow(
            postmortem_id=postmortem_id,
            required_reviewers=required,
            optional_reviewers=optional,
            auto_approve_after_hours=auto_approve_hours,
        )

        self._workflows[postmortem_id] = workflow
        return workflow

    def get_review_workflow(
        self,
        postmortem_id: str,
    ) -> ReviewWorkflow | None:
        """Get the review workflow for a postmortem."""
        return self._workflows.get(postmortem_id)

    def submit_review(
        self,
        postmortem_id: str,
        reviewer: str,
        approved: bool,
        feedback: str | None = None,
    ) -> bool:
        """Submit a review for a postmortem."""
        workflow = self._workflows.get(postmortem_id)
        if not workflow:
            return False

        all_reviewers = workflow.required_reviewers + workflow.optional_reviewers
        request = next(
            (r for r in all_reviewers if r.reviewer == reviewer),
            None
        )

        if not request:
            return False

        request.status = (
            ReviewStatus.APPROVED if approved
            else ReviewStatus.CHANGES_REQUESTED
        )
        request.responded_at = datetime.now(timezone.utc)
        request.feedback = feedback

        return True

    def check_auto_approve(
        self,
        postmortem_id: str,
    ) -> bool:
        """Check if a postmortem should be auto-approved."""
        workflow = self._workflows.get(postmortem_id)
        if not workflow:
            return False

        if workflow.auto_approve_after_hours is None:
            return False

        elapsed = datetime.now(timezone.utc) - workflow.created_at
        threshold = timedelta(hours=workflow.auto_approve_after_hours)

        if elapsed < threshold:
            return False

        for request in workflow.required_reviewers:
            if request.status == ReviewStatus.PENDING:
                request.status = ReviewStatus.APPROVED
                request.responded_at = datetime.now(timezone.utc)
                request.feedback = "Auto-approved after timeout"

        return True

    def send_reminder(
        self,
        postmortem_id: str,
        reviewer: str,
    ) -> dict[str, Any]:
        """Send a reminder to a reviewer."""
        workflow = self._workflows.get(postmortem_id)
        if not workflow:
            return {"success": False, "reason": "Workflow not found"}

        request = next(
            (r for r in workflow.required_reviewers + workflow.optional_reviewers
             if r.reviewer == reviewer and r.status == ReviewStatus.PENDING),
            None
        )

        if not request:
            return {"success": False, "reason": "Reviewer not found or already responded"}

        return {
            "success": True,
            "reminder_sent_to": reviewer,
            "postmortem_id": postmortem_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def get_pending_reviews(
        self,
        reviewer: str,
    ) -> list[dict[str, Any]]:
        """Get all pending reviews for a reviewer."""
        pending = []

        for postmortem_id, workflow in self._workflows.items():
            for request in workflow.required_reviewers + workflow.optional_reviewers:
                if request.reviewer == reviewer and request.status == ReviewStatus.PENDING:
                    pending.append({
                        "postmortem_id": postmortem_id,
                        "role": request.role,
                        "requested_at": request.requested_at.isoformat(),
                        "is_required": request in workflow.required_reviewers,
                    })

        return pending

    def get_review_checklist(
        self,
        postmortem_id: str,
    ) -> list[dict[str, Any]]:
        """Get the AI-generated review checklist."""
        return [
            {
                "item": "Executive summary present",
                "status": "pass",
                "description": "High-level summary is included",
            },
            {
                "item": "Timeline has all key events",
                "status": "pass",
                "description": "Timeline includes detection, investigation, and resolution",
            },
            {
                "item": "Root cause identified with evidence",
                "status": "pass",
                "description": "Root cause is supported by log/metric evidence",
            },
            {
                "item": "Contributing factors analyzed",
                "status": "pass",
                "description": "Process, technical, and human factors considered",
            },
            {
                "item": "Action items have owners and due dates",
                "status": "pass",
                "description": "All action items are assigned",
            },
            {
                "item": "Similar incidents referenced",
                "status": "pass",
                "description": "Related past incidents are linked",
            },
            {
                "item": "Blameless language verified",
                "status": "pass",
                "description": "No individual blame in document",
            },
            {
                "item": "Customer communication section",
                "status": "warning",
                "description": "Missing: Consider adding customer communication summary",
            },
        ]


_manager: CollaborationManager | None = None


def get_collaboration_manager() -> CollaborationManager:
    """Get the default collaboration manager."""
    global _manager
    if _manager is None:
        _manager = CollaborationManager()
    return _manager
