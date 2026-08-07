import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8000';
const DURATION = __ENV.K6_DURATION || '1h';
const VUS = parseInt(__ENV.K6_VUS) || 20;

// Metrics to detect memory leaks and degradation
const memoryLeak = new Trend('response_time_over_time');
const cumulativeErrors = new Counter('cumulative_errors');
const errorRate = new Rate('error_rate');

export const options = {
  stages: [
    { duration: '5m', target: VUS },     // Ramp up
    { duration: DURATION, target: VUS }, // Sustain for duration
    { duration: '5m', target: 0 },       // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<3000'],  // Slightly relaxed for long-running
    error_rate: ['rate<0.02'],          // 2% error rate acceptable
    http_req_failed: ['rate<0.02'],
  },
};

const headers = {
  'Content-Type': 'application/json',
};

let iteration = 0;

export default function () {
  iteration++;

  // Health check (most frequent)
  const healthRes = http.get(`${BASE_URL}/healthz`);
  const healthOk = check(healthRes, {
    'health check ok': (r) => r.status === 200,
  });
  if (!healthOk) cumulativeErrors.add(1);
  errorRate.add(!healthOk);
  memoryLeak.add(healthRes.timings.duration, { iteration: iteration });

  // Realistic API usage pattern
  if (iteration % 5 === 0) {
    // Every 5th request: list investigations
    const listRes = http.get(`${BASE_URL}/api/v1/investigations`, { headers });
    check(listRes, {
      'list ok': (r) => r.status !== 500,
    });
    memoryLeak.add(listRes.timings.duration, { iteration: iteration });
  }

  if (iteration % 20 === 0) {
    // Every 20th request: start investigation
    const payload = JSON.stringify({
      query: `soak test iteration ${iteration}`,
      context: { test: true },
    });
    const startRes = http.post(`${BASE_URL}/api/v1/investigations`, payload, { headers });
    check(startRes, {
      'start ok': (r) => r.status !== 500,
    });
  }

  if (iteration % 100 === 0) {
    // Every 100th: check metrics endpoint
    const metricsRes = http.get(`${BASE_URL}/metrics`);
    check(metricsRes, {
      'metrics ok': (r) => r.status === 200 || r.status === 404,
    });
  }

  // Variable sleep to simulate real traffic patterns
  sleep(Math.random() * 3 + 1);
}

export function handleSummary(data) {
  const metrics = data.metrics;

  let report = '\n=== SOAK TEST REPORT ===\n\n';

  report += `Test Duration: ${DURATION}\n`;
  report += `Virtual Users: ${VUS}\n\n`;

  report += 'Stability Metrics:\n';
  report += `  Total Requests: ${metrics.http_reqs?.values.count || 0}\n`;
  report += `  Cumulative Errors: ${metrics.cumulative_errors?.values.count || 0}\n`;
  report += `  Error Rate: ${((metrics.error_rate?.values.rate || 0) * 100).toFixed(3)}%\n\n`;

  report += 'Latency Over Time:\n';
  report += `  Average: ${metrics.http_req_duration?.values.avg?.toFixed(2) || 0}ms\n`;
  report += `  P95: ${metrics.http_req_duration?.values['p(95)']?.toFixed(2) || 0}ms\n`;
  report += `  P99: ${metrics.http_req_duration?.values['p(99)']?.toFixed(2) || 0}ms\n`;
  report += `  Max: ${metrics.http_req_duration?.values.max?.toFixed(2) || 0}ms\n\n`;

  // Check for memory leak indicators (latency should not increase significantly)
  const avgLatency = metrics.http_req_duration?.values.avg || 0;
  const maxLatency = metrics.http_req_duration?.values.max || 0;

  if (maxLatency > avgLatency * 10) {
    report += '⚠️  WARNING: Large latency spikes detected - possible memory pressure\n';
  }

  const errorRateVal = metrics.error_rate?.values.rate || 0;
  if (errorRateVal < 0.01) {
    report += '✅ STABLE: System maintained excellent performance over time\n';
  } else if (errorRateVal < 0.02) {
    report += '✅ ACCEPTABLE: Minor degradation within acceptable limits\n';
  } else if (errorRateVal < 0.05) {
    report += '⚠️  WARNING: Noticeable degradation over time - investigate\n';
  } else {
    report += '❌ UNSTABLE: System shows degradation under sustained load\n';
  }

  return {
    stdout: report,
  };
}
