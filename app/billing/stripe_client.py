"""A `StripeClient` implementation over the `stripe` SDK.

`app/api/billing_router.py` used to hand the imported `stripe` *module* straight
to `StripeBillingService`, which then calls `create_customer(...)`,
`create_checkout_session(...)`, `construct_webhook_event(...)` — none of which
exist on the module. So a deployment that installed the SDK and set
STRIPE_API_KEY got `AttributeError: module 'stripe' has no attribute
'create_customer'` on the first checkout, i.e. a 500; a deployment without the
SDK got a 503. There was no third configuration.

This maps the protocol onto the SDK's actual resource classes. It is a thin
translation layer on purpose: the request shapes stay in
`stripe_integration.py`, which is unit-testable against a fake.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("omnipotent.billing.stripe_client")


class StripeSDKClient:
    """The `StripeClient` protocol, backed by `stripe`."""

    def __init__(self, api_key: str):
        import stripe

        stripe.api_key = api_key
        self._stripe = stripe

    # --- customers ------------------------------------------------------------

    def create_customer(self, **kwargs: Any) -> Any:
        return self._stripe.Customer.create(**kwargs)

    def get_customer(self, customer_id: str) -> Any:
        return self._stripe.Customer.retrieve(customer_id)

    def update_customer(self, customer_id: str, **kwargs: Any) -> Any:
        return self._stripe.Customer.modify(customer_id, **kwargs)

    # --- hosted pages ---------------------------------------------------------

    def create_checkout_session(self, **kwargs: Any) -> Any:
        return self._stripe.checkout.Session.create(**kwargs)

    def create_billing_portal_session(self, **kwargs: Any) -> Any:
        return self._stripe.billing_portal.Session.create(**kwargs)

    # --- subscriptions --------------------------------------------------------

    def create_subscription(self, **kwargs: Any) -> Any:
        return self._stripe.Subscription.create(**kwargs)

    def get_subscription(self, subscription_id: str) -> Any:
        return self._stripe.Subscription.retrieve(subscription_id)

    def update_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        return self._stripe.Subscription.modify(subscription_id, **kwargs)

    def cancel_subscription(self, subscription_id: str, **kwargs: Any) -> Any:
        return self._stripe.Subscription.delete(subscription_id, **kwargs)

    # --- invoices -------------------------------------------------------------

    def get_invoice(self, invoice_id: str) -> Any:
        return self._stripe.Invoice.retrieve(invoice_id)

    def list_invoices(self, **kwargs: Any) -> Any:
        return self._stripe.Invoice.list(**kwargs)

    # --- payment methods ------------------------------------------------------

    def get_payment_method(self, payment_method_id: str) -> Any:
        return self._stripe.PaymentMethod.retrieve(payment_method_id)

    def list_payment_methods(self, **kwargs: Any) -> Any:
        return self._stripe.PaymentMethod.list(**kwargs)

    def attach_payment_method(self, payment_method_id: str, **kwargs: Any) -> Any:
        return self._stripe.PaymentMethod.attach(payment_method_id, **kwargs)

    def detach_payment_method(self, payment_method_id: str) -> Any:
        return self._stripe.PaymentMethod.detach(payment_method_id)

    # --- webhooks -------------------------------------------------------------

    def construct_webhook_event(
        self, payload: bytes, signature: str, secret: str
    ) -> Any:
        return self._stripe.Webhook.construct_event(payload, signature, secret)


def make_stripe_client(api_key: str) -> StripeSDKClient | None:
    """The SDK-backed client, or None when the key or the SDK is missing.

    None means "billing is not configured", which every caller already turns into
    a 503. An SDK that is installed but unusable is a configuration error worth
    seeing in the log rather than a silent downgrade.
    """
    if not api_key.strip():
        return None
    try:
        return StripeSDKClient(api_key.strip())
    except ImportError:
        log.warning(
            "STRIPE_API_KEY is set but the `stripe` package is not installed; "
            "billing endpoints will report 503. Install the `billing` extra."
        )
        return None
