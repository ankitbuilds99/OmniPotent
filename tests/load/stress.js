import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8000';

// Custom metrics to track breaking point
const errorRate = new Rate('errors');
const responseTrend = new Trend('response_time_trend');

export const options = {
  stages: [
    { duration: '2m', target: 50 },   // Warm up
    { duration: '3m', target: 100 },  // Normal load
    { duration: '3m', target: 200 },  // High load
    { duration: '3m', target: 300 },  // Stress
    { duration: '3m', target: 400 },  // Breaking point test
    { duration: '2m', target: 0 },    // Recovery
  ],
  thresholds: {
    http_req_duration: ['p(99)<10000'],  // Higher threshold for stress
    errors: ['rate<0.5'],  // Allow up to 50% errors to find breaking point
  },
};

export default function () {
  // Only use endpoints that return 200 without auth (open paths)
  const endpoints = [
    { path: '/healthz', weight: 3 },
    { path: '/readyz', weight: 2 },
    { path: '/frontend-config', weight: 2 },
    { path: '/metrics/slo', weight: 2 },
    { path: '/auth/providers', weight: 1 },
  ];

  // Weighted random selection
  const totalWeight = endpoints.reduce((sum, e) => sum + e.weight, 0);
  let random = Math.random() * totalWeight;
  let selected = endpoints[0];

  for (const endpoint of endpoints) {
    random -= endpoint.weight;
    if (random <= 0) {
      selected = endpoint;
      break;
    }
  }

  const start = Date.now();
  const res = http.get(`${BASE_URL}${selected.path}`, { timeout: '30s' });
  const duration = Date.now() - start;

  responseTrend.add(duration);

  const success = check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
    'response in time': (r) => r.timings.duration < 10000,
  });

  errorRate.add(!success);

  // Very short sleep to maximize load
  sleep(0.1);
}

export function handleSummary(data) {
  const metrics = data.metrics;

  let report = '\n=== STRESS TEST REPORT ===\n\n';

  report += 'Peak Load Metrics:\n';
  report += `  Max VUs: ${data.state.testRunDurationMs ? '400' : 'N/A'}\n`;
  report += `  Total Requests: ${metrics.http_reqs?.values.count || 0}\n`;
  report += `  Peak RPS: ${metrics.http_reqs?.values.rate?.toFixed(2) || 0}\n\n`;

  report += 'Latency Under Stress:\n';
  report += `  Median: ${metrics.http_req_duration?.values.med?.toFixed(2) || 0}ms\n`;
  report += `  P95: ${metrics.http_req_duration?.values['p(95)']?.toFixed(2) || 0}ms\n`;
  report += `  P99: ${metrics.http_req_duration?.values['p(99)']?.toFixed(2) || 0}ms\n`;
  report += `  Max: ${metrics.http_req_duration?.values.max?.toFixed(2) || 0}ms\n\n`;

  report += 'Error Analysis:\n';
  const errRate = metrics.http_req_failed?.values.rate || 0;
  report += `  Error Rate: ${(errRate * 100).toFixed(2)}%\n`;

  if (errRate < 0.01) {
    report += '  Status: EXCELLENT - System handles extreme load\n';
  } else if (errRate < 0.05) {
    report += '  Status: GOOD - Minor degradation under stress\n';
  } else if (errRate < 0.2) {
    report += '  Status: WARNING - Significant errors under stress\n';
  } else {
    report += '  Status: CRITICAL - System breaks under load\n';
  }

  return {
    stdout: report,
  };
}
