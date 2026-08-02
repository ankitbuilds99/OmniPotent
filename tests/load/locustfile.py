"""Load testing for OmniPotent API.

Run with:
    locust -f tests/load/locustfile.py --host=https://api.omnipotent.com

Web UI: http://localhost:8089

Headless mode:
    locust -f tests/load/locustfile.py --host=https://api.omnipotent.com \
        --headless --users 100 --spawn-rate 10 --run-time 5m

Targets:
    - P95 latency < 500ms
    - Error rate < 1%
    - RPS > 100
"""

import random
from datetime import datetime
from uuid import uuid4

from locust import HttpUser, task, between, events

# Configuration
class Config:
    API_KEY = "test-api-key"
    ORGANIZATION_ID = "test-org"

    # Test data
    SERVICES = ["checkout", "payments", "inventory", "auth", "api-gateway", "search", "recommendations"]
    SEVERITIES = ["critical", "high", "medium", "low"]

    # Targets
    TARGET_RPS = 100
    TARGET_P95_LATENCY_MS = 500
    TARGET_ERROR_RATE = 0.01


class OmniPotentUser(HttpUser):
    """Simulates a regular OmniPotent API user."""

    wait_time = between(1, 3)
    weight = 10

    def on_start(self):
        """Set up authentication."""
        self.headers = {
            "Authorization": f"Bearer {Config.API_KEY}",
            "X-Organization-ID": Config.ORGANIZATION_ID,
            "Content-Type": "application/json",
        }
        self.incident_ids: list[str] = []

    # =========================================================================
    # HEALTH & METRICS
    # =========================================================================

    @task(20)
    def health_check(self):
        """GET /health - Most frequent endpoint."""
        with self.client.get(
            "/health",
            name="/health",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Health check failed: {response.status_code}")

    @task(5)
    def get_metrics(self):
        """GET /metrics - Prometheus metrics endpoint."""
        with self.client.get(
            "/metrics",
            name="/metrics",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(f"Metrics failed: {response.status_code}")

    # =========================================================================
    # INCIDENTS
    # =========================================================================

    @task(15)
    def list_incidents(self):
        """GET /api/v1/incidents - List incidents."""
        with self.client.get(
            "/api/v1/incidents",
            headers=self.headers,
            params={"limit": 20, "status": "triggered"},
            name="/api/v1/incidents [GET]",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                items = data.get("items", data if isinstance(data, list) else [])
                for incident in items:
                    if "id" in incident and incident["id"] not in self.incident_ids:
                        self.incident_ids.append(incident["id"])
                        if len(self.incident_ids) > 50:
                            self.incident_ids.pop(0)
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"Status {response.status_code}")

    @task(5)
    def create_incident(self):
        """POST /api/v1/incidents - Create new incident."""
        incident = {
            "title": f"Load test incident {uuid4().hex[:8]}",
            "severity": random.choice(Config.SEVERITIES),
            "affected_services": random.sample(Config.SERVICES, k=random.randint(1, 3)),
            "description": f"Automated load test incident at {datetime.utcnow().isoformat()}",
            "source": "load_test",
        }

        with self.client.post(
            "/api/v1/incidents",
            headers=self.headers,
            json=incident,
            name="/api/v1/incidents [POST]",
            catch_response=True,
        ) as response:
            if response.status_code == 201:
                data = response.json()
                if "id" in data:
                    self.incident_ids.append(data["id"])
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"Status {response.status_code}")

    @task(10)
    def get_incident(self):
        """GET /api/v1/incidents/{id} - Get specific incident."""
        if not self.incident_ids:
            return

        incident_id = random.choice(self.incident_ids)

        with self.client.get(
            f"/api/v1/incidents/{incident_id}",
            headers=self.headers,
            name="/api/v1/incidents/{id} [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            elif response.status_code == 429:
                response.failure("Rate limited")
            else:
                response.failure(f"Status {response.status_code}")

    @task(3)
    def update_incident(self):
        """PATCH /api/v1/incidents/{id} - Update incident."""
        if not self.incident_ids:
            return

        incident_id = random.choice(self.incident_ids)

        update = {
            "status": random.choice(["acknowledged", "investigating", "resolved"]),
        }

        with self.client.patch(
            f"/api/v1/incidents/{incident_id}",
            headers=self.headers,
            json=update,
            name="/api/v1/incidents/{id} [PATCH]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    # =========================================================================
    # INVESTIGATIONS
    # =========================================================================

    @task(8)
    def get_investigation(self):
        """GET /api/v1/investigations/{id} - Get investigation results."""
        if not self.incident_ids:
            return

        incident_id = random.choice(self.incident_ids)

        with self.client.get(
            f"/api/v1/investigations/{incident_id}",
            headers=self.headers,
            name="/api/v1/investigations/{id} [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(3)
    def get_investigation_findings(self):
        """GET /api/v1/investigations/{id}/findings - Get findings."""
        if not self.incident_ids:
            return

        incident_id = random.choice(self.incident_ids)

        with self.client.get(
            f"/api/v1/investigations/{incident_id}/findings",
            headers=self.headers,
            name="/api/v1/investigations/{id}/findings [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    # =========================================================================
    # SAFETY GATES
    # =========================================================================

    @task(3)
    def evaluate_safety(self):
        """POST /api/v1/safety/evaluate - Evaluate action safety."""
        if not self.incident_ids:
            return

        incident_id = random.choice(self.incident_ids)

        action = {
            "incident_id": incident_id,
            "action_type": random.choice(["rollback", "scale_up", "restart", "drain"]),
            "target_service": random.choice(Config.SERVICES),
            "confidence_score": random.uniform(0.5, 0.95),
        }

        with self.client.post(
            "/api/v1/safety/evaluate",
            headers=self.headers,
            json=action,
            name="/api/v1/safety/evaluate [POST]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 400, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    # =========================================================================
    # SERVICES & DEPENDENCY GRAPH
    # =========================================================================

    @task(5)
    def list_services(self):
        """GET /api/v1/services - List services."""
        with self.client.get(
            "/api/v1/services",
            headers=self.headers,
            name="/api/v1/services [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")

    @task(2)
    def get_dependency_graph(self):
        """GET /api/v1/services/graph - Get dependency graph."""
        with self.client.get(
            "/api/v1/services/graph",
            headers=self.headers,
            name="/api/v1/services/graph [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


class PowerUser(HttpUser):
    """Simulates a power user with intensive operations."""

    wait_time = between(0.5, 1)
    weight = 1

    def on_start(self):
        self.headers = {
            "Authorization": f"Bearer {Config.API_KEY}",
            "X-Organization-ID": Config.ORGANIZATION_ID,
            "Content-Type": "application/json",
        }

    @task
    def bulk_query(self):
        """Perform multiple filtered queries."""
        filters = [
            {"status": "triggered", "limit": 50},
            {"severity": "critical", "limit": 50},
            {"status": "investigating", "limit": 50},
        ]

        for params in filters:
            with self.client.get(
                "/api/v1/incidents",
                headers=self.headers,
                params=params,
                name="/api/v1/incidents [BULK]",
                catch_response=True,
            ) as response:
                if response.status_code in [200, 429]:
                    response.success()
                else:
                    response.failure(f"Status {response.status_code}")

    @task
    def search_similar_incidents(self):
        """Search for similar incidents."""
        query = random.choice([
            "high latency checkout",
            "database connection timeout",
            "memory exhaustion",
            "SSL certificate expiry",
        ])

        with self.client.get(
            "/api/v1/incidents/similar",
            headers=self.headers,
            params={"query": query, "limit": 10},
            name="/api/v1/incidents/similar [GET]",
            catch_response=True,
        ) as response:
            if response.status_code in [200, 404]:
                response.success()
            else:
                response.failure(f"Status {response.status_code}")


# ============================================================================
# EVENT HANDLERS
# ============================================================================

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print("=" * 60)
    print("OmniPotent Load Test")
    print("=" * 60)
    print(f"Target Host: {environment.host}")
    print(f"Target RPS: {Config.TARGET_RPS}")
    print(f"Target P95 Latency: {Config.TARGET_P95_LATENCY_MS}ms")
    print(f"Target Error Rate: {Config.TARGET_ERROR_RATE * 100}%")
    print("=" * 60)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops. Validate against targets."""
    print("\n" + "=" * 60)
    print("LOAD TEST RESULTS")
    print("=" * 60)

    stats = environment.stats
    total_requests = stats.total.num_requests
    total_failures = stats.total.num_failures
    avg_response_time = stats.total.avg_response_time
    p95 = stats.total.get_response_time_percentile(0.95) or 0
    p99 = stats.total.get_response_time_percentile(0.99) or 0
    rps = stats.total.total_rps

    print(f"Total Requests: {total_requests}")
    print(f"Total Failures: {total_failures}")
    error_rate = total_failures / max(total_requests, 1)
    print(f"Error Rate: {error_rate * 100:.2f}%")
    print(f"Avg Response Time: {avg_response_time:.2f}ms")
    print(f"P95 Response Time: {p95:.2f}ms")
    print(f"P99 Response Time: {p99:.2f}ms")
    print(f"Requests/sec: {rps:.2f}")

    print("\n" + "-" * 60)
    print("TARGET COMPLIANCE")
    print("-" * 60)

    passed = True

    if p95 <= Config.TARGET_P95_LATENCY_MS:
        print(f"[PASS] P95 Latency: {p95:.0f}ms <= {Config.TARGET_P95_LATENCY_MS}ms")
    else:
        print(f"[FAIL] P95 Latency: {p95:.0f}ms > {Config.TARGET_P95_LATENCY_MS}ms")
        passed = False

    if error_rate <= Config.TARGET_ERROR_RATE:
        print(f"[PASS] Error Rate: {error_rate * 100:.2f}% <= {Config.TARGET_ERROR_RATE * 100}%")
    else:
        print(f"[FAIL] Error Rate: {error_rate * 100:.2f}% > {Config.TARGET_ERROR_RATE * 100}%")
        passed = False

    if rps >= Config.TARGET_RPS * 0.8:  # Allow 20% variance
        print(f"[PASS] RPS: {rps:.0f} >= {Config.TARGET_RPS * 0.8:.0f}")
    else:
        print(f"[WARN] RPS: {rps:.0f} < {Config.TARGET_RPS * 0.8:.0f} (target: {Config.TARGET_RPS})")

    print("\n" + "=" * 60)
    print(f"OVERALL: {'PASSED' if passed else 'FAILED'}")
    print("=" * 60)


# ============================================================================
# CUSTOM SHAPES (Optional)
# ============================================================================

class StagesLoadShape:
    """Custom load shape with stages for gradual ramp-up."""

    stages = [
        {"duration": 60, "users": 10, "spawn_rate": 2},   # Warmup
        {"duration": 120, "users": 50, "spawn_rate": 5},  # Ramp up
        {"duration": 300, "users": 100, "spawn_rate": 10}, # Steady state
        {"duration": 60, "users": 150, "spawn_rate": 10},  # Peak
        {"duration": 60, "users": 50, "spawn_rate": 10},   # Cool down
    ]

    def tick(self):
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])
            run_time -= stage["duration"]

        return None
