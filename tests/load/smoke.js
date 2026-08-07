import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8000';

export const options = {
  vus: 1,
  duration: '30s',
  thresholds: {
    http_req_duration: ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};

export default function () {
  // Health check - always returns 200
  const healthRes = http.get(`${BASE_URL}/healthz`);
  check(healthRes, {
    'healthz returns 200': (r) => r.status === 200,
    'healthz has status field': (r) => {
      try {
        return r.json().status !== undefined;
      } catch (e) {
        return false;
      }
    },
  });

  // Readiness check - returns 200 or 503
  const readyRes = http.get(`${BASE_URL}/readyz`);
  check(readyRes, {
    'readyz returns 200 or 503': (r) => r.status === 200 || r.status === 503,
  });

  // Frontend config - open path, always returns 200
  const frontendRes = http.get(`${BASE_URL}/frontend-config`);
  check(frontendRes, {
    'frontend-config returns 200': (r) => r.status === 200,
  });

  // Metrics SLO - open path, always returns 200
  const metricsRes = http.get(`${BASE_URL}/metrics/slo`);
  check(metricsRes, {
    'metrics/slo returns 200': (r) => r.status === 200,
  });

  sleep(1);
}
