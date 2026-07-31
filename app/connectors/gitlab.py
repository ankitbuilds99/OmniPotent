"""GitLab Connector - CI/CD and repository management."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("devcopilot.connectors.gitlab")


class GitLabConnector(Connector):
    """Connector for GitLab API.

    Supports:
    - Projects and repositories
    - Pipelines and jobs
    - Merge requests
    - Issues
    - Deployments
    """

    @property
    def connector_type(self) -> str:
        return "gitlab"

    def _default_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "PRIVATE-TOKEN": self.config.api_key,
        }

    def _encode_project(self, project: str) -> str:
        """URL-encode a project path."""
        return quote(project, safe="")

    async def health_check(self) -> bool:
        """Check GitLab is reachable."""
        try:
            response = await self._get("/api/v4/version")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def get_project(self, project: str) -> QueryResult:
        """Get project details.

        Args:
            project: Project path (e.g., "group/project")
        """
        start = time.time()

        try:
            response = await self._get(f"/api/v4/projects/{self._encode_project(project)}")
            data = response.json()

            return QueryResult(
                success=True,
                data=data,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pipelines(
        self,
        project: str,
        ref: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> QueryResult:
        """Get pipelines for a project.

        Args:
            project: Project path
            ref: Branch/tag filter
            status: Status filter (running, pending, success, failed, canceled)
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {"per_page": limit}
        if ref:
            params["ref"] = ref
        if status:
            params["status"] = status

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/pipelines",
                params=params,
            )
            pipelines = response.json()

            return QueryResult(
                success=True,
                data=pipelines,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(pipelines)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pipeline(self, project: str, pipeline_id: int) -> QueryResult:
        """Get pipeline details.

        Args:
            project: Project path
            pipeline_id: Pipeline ID
        """
        start = time.time()

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/pipelines/{pipeline_id}"
            )
            data = response.json()

            return QueryResult(
                success=True,
                data=data,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_pipeline_jobs(
        self,
        project: str,
        pipeline_id: int,
        scope: str | None = None,
    ) -> QueryResult:
        """Get jobs for a pipeline.

        Args:
            project: Project path
            pipeline_id: Pipeline ID
            scope: Filter (created, pending, running, failed, success, canceled)
        """
        start = time.time()

        params = {}
        if scope:
            params["scope"] = scope

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/pipelines/{pipeline_id}/jobs",
                params=params,
            )
            jobs = response.json()

            return QueryResult(
                success=True,
                data=jobs,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(jobs)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_job_log(self, project: str, job_id: int) -> QueryResult:
        """Get job log output.

        Args:
            project: Project path
            job_id: Job ID
        """
        start = time.time()

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/jobs/{job_id}/trace"
            )
            log_content = response.text

            return QueryResult(
                success=True,
                data={"log": log_content},
                duration_ms=int((time.time() - start) * 1000),
                metadata={"size_bytes": len(log_content)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_merge_requests(
        self,
        project: str,
        state: str = "opened",
        limit: int = 20,
    ) -> QueryResult:
        """Get merge requests.

        Args:
            project: Project path
            state: State filter (opened, closed, merged, all)
            limit: Max results
        """
        start = time.time()

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/merge_requests",
                params={"state": state, "per_page": limit},
            )
            mrs = response.json()

            return QueryResult(
                success=True,
                data=mrs,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(mrs)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_deployments(
        self,
        project: str,
        environment: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> QueryResult:
        """Get deployments.

        Args:
            project: Project path
            environment: Environment filter
            status: Status filter (created, running, success, failed, canceled)
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {"per_page": limit, "order_by": "created_at", "sort": "desc"}
        if environment:
            params["environment"] = environment
        if status:
            params["status"] = status

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/deployments",
                params=params,
            )
            deployments = response.json()

            return QueryResult(
                success=True,
                data=deployments,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(deployments)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_commits(
        self,
        project: str,
        ref: str = "main",
        since: datetime | None = None,
        limit: int = 20,
    ) -> QueryResult:
        """Get commits.

        Args:
            project: Project path
            ref: Branch/tag
            since: Start date
            limit: Max results
        """
        start = time.time()

        params: dict[str, Any] = {"ref_name": ref, "per_page": limit}
        if since:
            params["since"] = since.isoformat()

        try:
            response = await self._get(
                f"/api/v4/projects/{self._encode_project(project)}/repository/commits",
                params=params,
            )
            commits = response.json()

            return QueryResult(
                success=True,
                data=commits,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(commits)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def retry_pipeline(self, project: str, pipeline_id: int) -> QueryResult:
        """Retry a failed pipeline.

        Args:
            project: Project path
            pipeline_id: Pipeline ID to retry
        """
        start = time.time()

        try:
            response = await self._post(
                f"/api/v4/projects/{self._encode_project(project)}/pipelines/{pipeline_id}/retry"
            )
            data = response.json()

            return QueryResult(
                success=True,
                data=data,
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )
