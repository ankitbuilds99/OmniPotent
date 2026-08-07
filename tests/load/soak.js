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

let iteration = 0;

export default function () {
  iteration++;

  // Health check (most frequent) - always returns 200
  const healthRes = http.get(`${BASE_URL}/healthz`);
  const healthOk = check(healthRes, {
    'healthz returns 200': (r) => r.status === 200,
  });
  if (!healthOk) cumulativeErrors.add(1);
  errorRate.add(!healthOk);
  memoryLeak.add(healthRes.timings.duration, { iteration: iteration });

  // Realistic usage pattern with open endpoints only
  if (iteration % 5 === 0) {
    // Every 5th request: frontend config
    const configRes = http.get(`${BASE_URL}/frontend-config`);
    const configOk = check(configRes, {
      'frontend-config returns 200': (r) => r.status === 200,
    });
    if (!configOk) cumulativeErrors.add(1);
    errorRate.add(!configOk);
    memoryLeak.add(configRes.timings.duration, { iteration: iteration });
  }

  if (iteration % 10 === 0) {
    // Every 10th: readiness check
    const readyRes = http.get(`${BASE_URL}/readyz`);
    const readyOk = check(readyRes, {
      'readyz returns 200 or 503': (r) => r.status === 200 || r.status === 503,
    });
    // Only count as error if not 200 or 503
    if (!readyOk) cumulativeErrors.add(1);
    errorRate.add(!readyOk);
  }

  if (iteration % 20 === 0) {
    // Every 20th: auth providers
    const authRes = http.get(`${BASE_URL}/auth/providers`);
    const authOk = check(authRes, {
      'auth/providers returns 200': (r) => r.status === 200,
    });
    if (!authOk) cumulativeErrors.add(1);
    errorRate.add(!authOk);
  }

  if (iteration % 100 === 0) {
    // Every 100th: check SLO metrics endpoint
    const metricsRes = http.get(`${BASE_URL}/metrics/slo`);
    const metricsOk = check(metricsRes, {
      'metrics/slo returns 200': (r) => r.status === 200,
    });
    if (!metricsOk) cumulativeErrors.add(1);
    errorRate.add(!metricsOk);
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
    report += '  WARNING: Large latency spikes detected - possible memory pressure\n';
  }

  const errorRateVal = metrics.error_rate?.values.rate || 0;
  if (errorRateVal < 0.01) {
    report += '  STABLE: System maintained excellent performance over time\n';
  } else if (errorRateVal < 0.02) {
    report += '  ACCEPTABLE: Minor degradation within acceptable limits\n';
  } else if (errorRateVal < 0.05) {
    report += '  WARNING: Noticeable degradation over time - investigate\n';
  } else {
    report += '  UNSTABLE: System shows degradation under sustained load\n';
  }

  return {
    stdout: report,
  };
}
