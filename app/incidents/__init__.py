"""Incidents management module."""

from app.incidents.router import router as incidents_router
from app.operator_annotations_api import router as annotation_router

incidents_router.include_router(annotation_router)

__all__ = ["incidents_router"]
