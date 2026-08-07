/**
 * K6 Load Testing Suite for OmniPotent
 *
 * Tests only open endpoints that don't require authentication, ensuring
 * the load tests work in CI without credentials.
 *
 * Scenarios: smoke, load, stress, spike, soak
 * Run: k6 run tests/load/k6_scenarios.js --env BASE_URL=http://localhost:8000
 */

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Counter, Rate, Trend } from 'k6/metrics';
import { randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const requestDuration = new Trend('request_duration_ms');
const errorRate = new Rate('errors');
const requestCount = new Counter('requests_total');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// Open endpoints that don't require authentication
const OPEN_ENDPOINTS = [
  '/healthz',
  '/readyz',
  '/frontend-config',
  '/metrics/slo',
  '/auth/providers',
];

// Test scenarios
export const options = {
  scenarios: {
    // Smoke test: Quick validation
    smoke: {
      executor: 'constant-vus',
      vus: 1,
      duration: '1m',
      tags: { scenario: 'smoke' },
      exec: 'smokeTest',
    },

    // Load test: Sustained normal traffic
    load: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 20 },   // Ramp up
        { duration: '5m', target: 20 },   // Sustained load
        { duration: '2m', target: 0 },    // Ramp down
      ],
      tags: { scenario: 'load' },
      exec: 'loadTest',
    },

    // Stress test: Find breaking point
    stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '2m', target: 50 },
        { duration: '5m', target: 100 },
        { duration: '2m', target: 150 },
        { duration: '5m', target: 150 },  // Hold at peak
        { duration: '2m', target: 0 },
      ],
      tags: { scenario: 'stress' },
      exec: 'stressTest',
    },

    // Spike test: Sudden burst
    spike: {
      executor: 'ramping-vus',
      startVUs: 5,
      stages: [
        { duration: '1m', target: 5 },     // Normal
        { duration: '10s', target: 100 },  // Spike!
        { duration: '2m', target: 100 },   // Hold spike
        { duration: '10s', target: 5 },    // Drop
        { duration: '1m', target: 5 },     // Recovery
      ],
      tags: { scenario: 'spike' },
      exec: 'spikeTest',
    },

    // Soak test: Long-running stability
    soak: {
      executor: 'constant-vus',
      vus: 30,
      duration: '30m',
      tags: { scenario: 'soak' },
      exec: 'soakTest',
    },
  },

  thresholds: {
    // SLO thresholds
    http_req_duration: ['p(95)<2000', 'p(99)<5000'],
    http_req_failed: ['rate<0.01'],  // <1% error rate
    errors: ['rate<0.05'],  // <5% error rate

    // Per-scenario thresholds
    'http_req_duration{scenario:smoke}': ['p(95)<500'],
    'http_req_duration{scenario:load}': ['p(95)<2000'],
    'http_req_duration{scenario:stress}': ['p(95)<5000'],
  },
};

// Smoke test: Basic functionality with open endpoints
export function smokeTest() {
  group('Health Checks', () => {
    const healthz = http.get(`${BASE_URL}/healthz`);
    check(healthz, {
      'healthz returns 200': (r) => r.status === 200,
      'healthz response valid': (r) => {
        try {
          return r.json('status') === 'healthy';
        } catch (e) {
          return false;
        }
      },
    });

    const readyz = http.get(`${BASE_URL}/readyz`);
    check(readyz, {
      'readyz returns 200 or 503': (r) => r.status === 200 || r.status === 503,
    });
  });

  group('Open Endpoints', () => {
    const frontend = http.get(`${BASE_URL}/frontend-config`);
    check(frontend, {
      'frontend-config returns 200': (r) => r.status === 200,
    });

    const metrics = http.get(`${BASE_URL}/metrics/slo`);
    check(metrics, {
      'metrics/slo returns 200': (r) => r.status === 200,
    });
  });

  sleep(1);
}

// Load test: Sustained traffic on open endpoints
export function loadTest() {
  const endpoint = OPEN_ENDPOINTS[randomIntBetween(0, OPEN_ENDPOINTS.length - 1)];

  const start = Date.now();
  const res = http.get(`${BASE_URL}${endpoint}`);
  const duration = Date.now() - start;

  requestDuration.add(duration);
  requestCount.add(1);

  const success = check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
  });

  if (!success) {
    errorRate.add(1);
  }

  sleep(randomIntBetween(1, 3));
}

// Stress test: High concurrency on open endpoints
export function stressTest() {
  const endpoint = OPEN_ENDPOINTS[randomIntBetween(0, OPEN_ENDPOINTS.length - 1)];

  const start = Date.now();
  const res = http.get(`${BASE_URL}${endpoint}`);
  const duration = Date.now() - start;

  requestDuration.add(duration);
  requestCount.add(1);

  const success = check(res, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
  });

  if (!success) {
    errorRate.add(1);
  }

  sleep(randomIntBetween(0, 1));
}

// Spike test: Sudden burst handling
export function spikeTest() {
  const endpoint = OPEN_ENDPOINTS[randomIntBetween(0, OPEN_ENDPOINTS.length - 1)];

  const start = Date.now();
  const res = http.get(`${BASE_URL}${endpoint}`);
  const duration = Date.now() - start;

  requestDuration.add(duration);
  requestCount.add(1);

  // 429 is acceptable during spikes (rate limiting working)
  const success = check(res, {
    'spike request handled': (r) => r.status === 200 || r.status === 429 || r.status === 503,
  });

  if (!success) {
    errorRate.add(1);
  }

  sleep(0.5);
}

// Soak test: Long-running stability
export function soakTest() {
  group('Endpoint Rotation', () => {
    const endpoint = OPEN_ENDPOINTS[randomIntBetween(0, OPEN_ENDPOINTS.length - 1)];

    const start = Date.now();
    const res = http.get(`${BASE_URL}${endpoint}`);
    const duration = Date.now() - start;

    requestDuration.add(duration);
    requestCount.add(1);

    const success = check(res, {
      'soak response ok': (r) => r.status === 200 || r.status === 503,
      'soak response time ok': (r) => r.timings.duration < 5000,
    });

    if (!success) {
      errorRate.add(1);
    }
  });

  sleep(randomIntBetween(2, 5));
}

// Default test function (required when running with --env K6_SCENARIO override)
export default function() {
  loadTest();
}

// Summary handler
export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
  };
}

function textSummary(data, options) {
  let output = '\n=== OmniPotent Load Test Summary ===\n\n';

  // Thresholds
  output += 'Thresholds:\n';
  for (const [name, threshold] of Object.entries(data.metrics)) {
    if (threshold.thresholds) {
      const status = Object.values(threshold.thresholds).every(t => t.ok) ? 'PASS' : 'FAIL';
      output += `  ${status}: ${name}\n`;
    }
  }

  // Key metrics
  const reqDuration = data.metrics.http_req_duration;
  if (reqDuration && reqDuration.values) {
    output += '\nLatency:\n';
    output += `  avg: ${(reqDuration.values.avg || 0).toFixed(2)}ms\n`;
    output += `  p95: ${(reqDuration.values['p(95)'] || 0).toFixed(2)}ms\n`;
    output += `  p99: ${(reqDuration.values['p(99)'] || 0).toFixed(2)}ms\n`;
  }

  const reqs = data.metrics.http_reqs;
  if (reqs && reqs.values) {
    output += '\nThroughput:\n';
    output += `  total: ${reqs.values.count || 0}\n`;
    output += `  rate: ${(reqs.values.rate || 0).toFixed(2)}/s\n`;
  }

  const failed = data.metrics.http_req_failed;
  if (failed && failed.values) {
    output += '\nErrors:\n';
    output += `  rate: ${((failed.values.rate || 0) * 100).toFixed(2)}%\n`;
  }

  return output;
}
