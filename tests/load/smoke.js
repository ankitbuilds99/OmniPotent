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
  // Health check
  const healthRes = http.get(`${BASE_URL}/healthz`);
  check(healthRes, {
    'health check status is 200': (r) => r.status === 200,
    'health check response has status': (r) => r.json().status !== undefined,
  });

  // Readiness check
  const readyRes = http.get(`${BASE_URL}/readyz`);
  check(readyRes, {
    'readiness check status is 200': (r) => r.status === 200,
  });

  // API root
  const rootRes = http.get(`${BASE_URL}/`);
  check(rootRes, {
    'root endpoint responds': (r) => r.status === 200 || r.status === 404,
  });

  // OpenAPI docs
  const docsRes = http.get(`${BASE_URL}/docs`);
  check(docsRes, {
    'docs endpoint responds': (r) => r.status === 200,
  });

  sleep(1);
}
