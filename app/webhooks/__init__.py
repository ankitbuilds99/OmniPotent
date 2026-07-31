"""Webhook receivers for auto-triggering investigations."""

from app.webhooks.router import router as webhooks_router

__all__ = ["webhooks_router"]
