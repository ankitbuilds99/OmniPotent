import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8000';
const VUS = parseInt(__ENV.K6_VUS) || 50;
const DURATION = __ENV.K6_DURATION || '1m';

// Custom metrics
const apiErrors = new Counter('api_errors');
const successRate = new Rate('success_rate');

export const options = {
  stages: [
    { duration: '30s', target: VUS / 2 },  // Ramp up
    { duration: DURATION, target: VUS },    // Sustain
    { duration: '30s', target: 0 },         // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    // All tested endpoints return 200 - any failure is real
    http_req_failed: ['rate<0.01'],
    success_rate: ['rate>0.99'],
  },
};

export default function () {
  // Only test endpoints that return 200 without authentication.
  // These are the open paths defined in _OPEN_PATHS in main.py.

  group('Health Endpoints', function () {
    const healthRes = http.get(`${BASE_URL}/healthz`);
    const healthOk = check(healthRes, {
      'healthz returns 200': (r) => r.status === 200,
    });
    successRate.add(healthOk);
    if (!healthOk) apiErrors.add(1);

    const readyRes = http.get(`${BASE_URL}/readyz`);
    const readyOk = check(readyRes, {
      // readyz returns 503 if not ready, which is valid
      'readyz returns 200 or 503': (r) => r.status === 200 || r.status === 503,
    });
    successRate.add(readyOk);
    // Only count 5xx errors (not 503 from readiness check)
    if (readyRes.status >= 500 && readyRes.status !== 503) apiErrors.add(1);
  });

  group('Public API Endpoints', function () {
    // Frontend config - open path, no auth required
    const frontendRes = http.get(`${BASE_URL}/frontend-config`);
    const frontendOk = check(frontendRes, {
      'frontend-config returns 200': (r) => r.status === 200,
    });
    successRate.add(frontendOk);
    if (!frontendOk) apiErrors.add(1);

    // Auth providers - open path, no auth required
    const authRes = http.get(`${BASE_URL}/auth/providers`);
    const authOk = check(authRes, {
      'auth/providers returns 200': (r) => r.status === 200,
    });
    successRate.add(authOk);
    if (!authOk) apiErrors.add(1);
  });

  group('Metrics Endpoints', function () {
    // SLO metrics - open path, no auth required
    const metricsRes = http.get(`${BASE_URL}/metrics/slo`);
    const metricsOk = check(metricsRes, {
      'metrics/slo returns 200': (r) => r.status === 200,
    });
    successRate.add(metricsOk);
    if (!metricsOk) apiErrors.add(1);
  });

  sleep(Math.random() * 2 + 1);
}

export function handleSummary(data) {
  return {
    stdout: textSummary(data, { indent: ' ', enableColors: true }),
  };
}

function textSummary(data, options) {
  const metrics = data.metrics;

  let summary = '\n=== Load Test Summary ===\n\n';

  if (metrics.http_req_duration && metrics.http_req_duration.values) {
    const duration = metrics.http_req_duration.values;
    summary += `HTTP Request Duration:\n`;
    summary += `  avg: ${(duration.avg || 0).toFixed(2)}ms\n`;
    summary += `  p95: ${(duration.p95 || duration['p(95)'] || 0).toFixed(2)}ms\n`;
    summary += `  p99: ${(duration.p99 || duration['p(99)'] || 0).toFixed(2)}ms\n\n`;
  }

  if (metrics.http_reqs && metrics.http_reqs.values) {
    summary += `Requests:\n`;
    summary += `  total: ${metrics.http_reqs.values.count || 0}\n`;
    summary += `  rate: ${(metrics.http_reqs.values.rate || 0).toFixed(2)}/s\n\n`;
  }

  if (metrics.http_req_failed && metrics.http_req_failed.values) {
    summary += `Failures:\n`;
    summary += `  rate: ${((metrics.http_req_failed.values.rate || 0) * 100).toFixed(2)}%\n\n`;
  }

  return summary;
}
