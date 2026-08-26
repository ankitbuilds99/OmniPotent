"""Customer and trial management.

Provides:
- Customer onboarding tracking
- Trial management
- Usage metering
- Conversion funnel analytics

Every function here is a coroutine and reads/writes the database — this is the
`customers` / `trials` / `customer_usage_records` tables, not the module dicts
it used to be, and every row is scoped to the calling org. See
:mod:`app.customers.manager` for why this state cannot live in the process.
"""

from app.customers.manager import (
    CustomerManager,
    Customer,
    CustomerTier,
    CustomerStatus,
    Trial,
    TrialStatus,
    clear_customers,
    create_customer,
    create_trial,
    get_customer,
    get_trial,
    get_customer_stats,
    get_customer_usage,
    listed_mrr_cents,
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
    "clear_customers",
    "create_customer",
    "create_trial",
    "get_customer",
    "get_trial",
    "get_customer_stats",
    "get_customer_usage",
    "listed_mrr_cents",
    "record_usage",
    "convert_trial",
    "get_active_trials",
]
