"""Minimal separately deployable customer-runner boundary.

The control plane can ask this service to validate a narrowly scoped capability,
but it cannot provide credentials or arbitrary commands. A deployment must inject
the customer-side executor explicitly; the default service fails closed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from app.customer_runner import (
    RunnerExecutor,
    RunnerRequest,
    capability_from_payload,
    validate_capability,
)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: dict[str, Any]
    request: RunnerRequest


def create_runner_app(executor: RunnerExecutor | None = None) -> FastAPI:
    app = FastAPI(title="OmniPotent Customer Runner", version="1")

    @app.get("/health")
    def health() -> dict[str, Any]:
        configured = executor is not None
        return {
            "status": "ok" if configured else "degraded",
            "ready": configured,
            "executor_configured": configured,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.post("/v1/execute")
    def execute(body: ExecuteRequest) -> dict[str, Any]:
        try:
            capability = capability_from_payload(body.capability)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid_capability: {exc}") from exc
        allowed, reason = validate_capability(capability, body.request)
        if not allowed:
            raise HTTPException(status_code=403, detail=reason)
        if executor is None:
            raise HTTPException(status_code=503, detail="customer_executor_not_configured")
        result = executor.execute(body.request)
        return {"status": "submitted", "token_id": capability.token_id, "result": result}

    return app


app = create_runner_app()
