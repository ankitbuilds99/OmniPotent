import http from 'k6/http';
import { check, sleep, group } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8000';
const VUS = parseInt(__ENV.K6_VUS) || 50;
const DURATION = __ENV.K6_DURATION || '1m';

// Custom metrics
const investigationDuration = new Trend('investigation_duration');
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
    http_req_failed: ['rate<0.01'],
    success_rate: ['rate>0.95'],
  },
};

const headers = {
  'Content-Type': 'application/json',
  'Authorization': 'Bearer test-token',
};

export default function () {
  group('Health Endpoints', function () {
    const healthRes = http.get(`${BASE_URL}/healthz`);
    check(healthRes, {
      'health status 200': (r) => r.status === 200,
    });

    const readyRes = http.get(`${BASE_URL}/readyz`);
    check(readyRes, {
      'ready status 200': (r) => r.status === 200,
    });
  });

  group('API Endpoints', function () {
    // List investigations
    const listRes = http.get(`${BASE_URL}/api/v1/investigations`, { headers });
    const listOk = check(listRes, {
      'list status 200 or 401': (r) => r.status === 200 || r.status === 401,
    });
    successRate.add(listOk);
    if (!listOk) apiErrors.add(1);

    // Start investigation (mock)
    const startPayload = JSON.stringify({
      query: 'checkout-api 500 errors',
      context: {
        service: 'checkout-api',
        timerange: '1h',
      },
    });

    const startRes = http.post(
      `${BASE_URL}/api/v1/investigations`,
      startPayload,
      { headers }
    );
    const startOk = check(startRes, {
      'start investigation status': (r) => r.status === 200 || r.status === 201 || r.status === 401 || r.status === 422,
    });
    successRate.add(startOk);
    if (startRes.status >= 500) apiErrors.add(1);
  });

  group('Webhook Endpoints', function () {
    // PagerDuty webhook
    const pdPayload = JSON.stringify({
      event: {
        event_type: 'incident.triggered',
        data: {
          id: 'P123456',
          title: 'High CPU on checkout-api',
          service: { summary: 'checkout-api' },
          html_url: 'https://pagerduty.com/incidents/P123456',
        },
      },
    });

    const pdRes = http.post(
      `${BASE_URL}/api/v1/webhooks/pagerduty`,
      pdPayload,
      {
        headers: {
          'Content-Type': 'application/json',
          'X-PagerDuty-Signature': 'v1=test',
        },
      }
    );
    check(pdRes, {
      'pagerduty webhook accepted': (r) => r.status !== 500,
    });
  });

  group('Metrics Endpoints', function () {
    const metricsRes = http.get(`${BASE_URL}/metrics`);
    check(metricsRes, {
      'metrics endpoint responds': (r) => r.status === 200 || r.status === 404,
    });
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
