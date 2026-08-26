"""Waitlist and early access management.

Provides:
- Email capture and validation
- Waitlist signup tracking
- Early access tier management
- Conversion funnel analytics

Every function here is a coroutine: the waitlist is the `waitlist_entries`
table, not the module dict it used to be.
"""

from app.waitlist.manager import (
    WaitlistManager,
    WaitlistEntry,
    WaitlistTier,
    SignupSource,
    add_to_waitlist,
    clear_waitlist,
    get_waitlist_entry,
    get_waitlist_stats,
    get_waitlist_entries,
    mark_converted,
    promote_to_early_access,
    export_waitlist,
)

__all__ = [
    "WaitlistManager",
    "WaitlistEntry",
    "WaitlistTier",
    "SignupSource",
    "add_to_waitlist",
    "clear_waitlist",
    "get_waitlist_entry",
    "get_waitlist_stats",
    "get_waitlist_entries",
    "mark_converted",
    "promote_to_early_access",
    "export_waitlist",
]
