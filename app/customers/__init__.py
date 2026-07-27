"""Customer and trial management.

Provides:
- Customer onboarding tracking
- Trial management
- Usage metering
- Conversion funnel analytics
"""

from app.customers.manager import (
    CustomerManager,
    Customer,
    CustomerTier,
    CustomerStatus,
    Trial,
    TrialStatus,
    create_customer,
    create_trial,
    get_customer,
    get_trial,
    get_customer_stats,
    record_usage,
    convert_trial,
    get_active_trials,
)

__all__ = [
    "CustomerManager",
    "Customer",
    "CustomerTier",
    "CustomerStatus",
    "Trial",
    "TrialStatus",
    "create_customer",
    "create_trial",
    "get_customer",
    "get_trial",
    "get_customer_stats",
    "record_usage",
    "convert_trial",
    "get_active_trials",
]
