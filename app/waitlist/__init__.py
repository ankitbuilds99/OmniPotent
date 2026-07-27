"""Waitlist and early access management.

Provides:
- Email capture and validation
- Waitlist signup tracking
- Early access tier management
- Conversion funnel analytics
"""

from app.waitlist.manager import (
    WaitlistManager,
    WaitlistEntry,
    WaitlistTier,
    SignupSource,
    add_to_waitlist,
    get_waitlist_entry,
    get_waitlist_stats,
    get_waitlist_entries,
    promote_to_early_access,
    export_waitlist,
)

__all__ = [
    "WaitlistManager",
    "WaitlistEntry",
    "WaitlistTier",
    "SignupSource",
    "add_to_waitlist",
    "get_waitlist_entry",
    "get_waitlist_stats",
    "get_waitlist_entries",
    "promote_to_early_access",
    "export_waitlist",
]
