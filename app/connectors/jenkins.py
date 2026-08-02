"""Jenkins Connector - Build automation and CI."""

from __future__ import annotations

import base64
import logging
import time
from datetime import datetime, timezone

from app.connectors.base import Connector, ConnectorError, QueryResult

log = logging.getLogger("omnipotent.connectors.jenkins")


class JenkinsConnector(Connector):
    """Connector for Jenkins CI/CD.

    Supports:
    - Jobs and builds
    - Build logs
    - Queue management
    - Node/agent status
    """

    @property
    def connector_type(self) -> str:
        return "jenkins"

    def _default_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}

        # Jenkins uses Basic auth
        if self.config.username and self.config.api_key:
            credentials = f"{self.config.username}:{self.config.api_key}"
            encoded = base64.b64encode(credentials.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        return headers

    async def health_check(self) -> bool:
        """Check Jenkins is reachable."""
        try:
            response = await self._get("/api/json")
            return response.status_code == 200
        except ConnectorError:
            return False

    async def get_jobs(self, folder: str | None = None) -> QueryResult:
        """Get all jobs.

        Args:
            folder: Optional folder path
        """
        start = time.time()

        path = f"/job/{folder}/api/json" if folder else "/api/json"

        try:
            response = await self._get(path, params={"tree": "jobs[name,url,color,lastBuild[number,result,timestamp]]"})
            data = response.json()

            jobs = data.get("jobs", [])

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

    async def get_job(self, job_name: str) -> QueryResult:
        """Get job details.

        Args:
            job_name: Job name (can include folder path)
        """
        start = time.time()

        # Handle folder paths
        job_path = job_name.replace("/", "/job/")

        try:
            response = await self._get(f"/job/{job_path}/api/json")
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

    async def get_builds(
        self,
        job_name: str,
        limit: int = 10,
    ) -> QueryResult:
        """Get builds for a job.

        Args:
            job_name: Job name
            limit: Max builds to return
        """
        start = time.time()
        job_path = job_name.replace("/", "/job/")

        try:
            response = await self._get(
                f"/job/{job_path}/api/json",
                params={"tree": f"builds[number,result,timestamp,duration,building]{{0,{limit}}}"},
            )
            data = response.json()

            builds = data.get("builds", [])

            # Convert timestamps
            for build in builds:
                if build.get("timestamp"):
                    build["timestamp_iso"] = datetime.fromtimestamp(
                        build["timestamp"] / 1000, tz=timezone.utc
                    ).isoformat()

            return QueryResult(
                success=True,
                data=builds,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(builds)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_build(self, job_name: str, build_number: int) -> QueryResult:
        """Get build details.

        Args:
            job_name: Job name
            build_number: Build number
        """
        start = time.time()
        job_path = job_name.replace("/", "/job/")

        try:
            response = await self._get(f"/job/{job_path}/{build_number}/api/json")
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

    async def get_build_log(
        self,
        job_name: str,
        build_number: int,
        start_line: int = 0,
    ) -> QueryResult:
        """Get build console output.

        Args:
            job_name: Job name
            build_number: Build number
            start_line: Starting line (for progressive fetch)
        """
        start = time.time()
        job_path = job_name.replace("/", "/job/")

        try:
            response = await self._get(
                f"/job/{job_path}/{build_number}/logText/progressiveText",
                params={"start": start_line},
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

    async def get_queue(self) -> QueryResult:
        """Get the build queue."""
        start = time.time()

        try:
            response = await self._get("/queue/api/json")
            data = response.json()

            items = data.get("items", [])

            return QueryResult(
                success=True,
                data=items,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(items)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def get_nodes(self) -> QueryResult:
        """Get build nodes/agents."""
        start = time.time()

        try:
            response = await self._get(
                "/computer/api/json",
                params={"tree": "computer[displayName,offline,temporarilyOffline,idle,numExecutors]"},
            )
            data = response.json()

            nodes = data.get("computer", [])

            return QueryResult(
                success=True,
                data=nodes,
                duration_ms=int((time.time() - start) * 1000),
                metadata={"count": len(nodes)},
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data=[],
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def trigger_build(
        self,
        job_name: str,
        parameters: dict[str, str] | None = None,
    ) -> QueryResult:
        """Trigger a build.

        Args:
            job_name: Job name
            parameters: Optional build parameters
        """
        start = time.time()
        job_path = job_name.replace("/", "/job/")

        endpoint = f"/job/{job_path}/build"
        if parameters:
            endpoint = f"/job/{job_path}/buildWithParameters"

        try:
            response = await self._post(endpoint, params=parameters or {})

            # Jenkins returns 201 Created for successful build trigger
            success = response.status_code in (200, 201)

            return QueryResult(
                success=success,
                data={"queued": success},
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )

    async def stop_build(self, job_name: str, build_number: int) -> QueryResult:
        """Stop a running build.

        Args:
            job_name: Job name
            build_number: Build number to stop
        """
        start = time.time()
        job_path = job_name.replace("/", "/job/")

        try:
            response = await self._post(f"/job/{job_path}/{build_number}/stop")

            return QueryResult(
                success=response.status_code in (200, 302),
                data={"stopped": True},
                duration_ms=int((time.time() - start) * 1000),
            )

        except ConnectorError as e:
            return QueryResult(
                success=False,
                data={},
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
            )
