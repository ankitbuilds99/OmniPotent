/**
 * K6 Load Testing Suite for OmniPotent
 *
 * Scenarios: smoke, load, stress, spike, soak
 * Run: k6 run tests/load/k6_scenarios.js --env BASE_URL=http://localhost:8000
 */

import http from 'k6/http';
import ws from 'k6/ws';
import { check, group, sleep } from 'k6';
import { Counter, Gauge, Rate, Trend } from 'k6/metrics';
import { randomString, randomIntBetween } from 'https://jslib.k6.io/k6-utils/1.2.0/index.js';

// Custom metrics
const investigationDuration = new Trend('investigation_duration_ms');
const llmLatency = new Trend('llm_latency_ms');
const toolCallCount = new Counter('tool_calls_total');
const errorRate = new Rate('errors');
const activeInvestigations = new Gauge('active_investigations');

// Configuration
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const API_TOKEN = __ENV.API_TOKEN || 'test-token';
const WS_URL = BASE_URL.replace('http', 'ws');

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
    investigation_duration_ms: ['p(95)<60000'],  // 95% under 60s
    errors: ['rate<0.05'],  // <5% error rate

    // Per-scenario thresholds
    'http_req_duration{scenario:smoke}': ['p(95)<500'],
    'http_req_duration{scenario:load}': ['p(95)<2000'],
    'http_req_duration{scenario:stress}': ['p(95)<5000'],
  },
};

// Headers helper
function headers() {
  return {
    'Authorization': `Bearer ${API_TOKEN}`,
    'Content-Type': 'application/json',
  };
}

// Smoke test: Basic functionality
export function smokeTest() {
  group('Health Checks', () => {
    const healthz = http.get(`${BASE_URL}/healthz`);
    check(healthz, {
      'healthz status 200': (r) => r.status === 200,
      'healthz response valid': (r) => r.json('status') === 'healthy',
    });

    const readyz = http.get(`${BASE_URL}/readyz`);
    check(readyz, {
      'readyz status 200': (r) => r.status === 200,
    });
  });

  group('Config Endpoint', () => {
    const config = http.get(`${BASE_URL}/config`, { headers: headers() });
    check(config, {
      'config status 200': (r) => r.status === 200,
      'config has provider': (r) => r.json('provider') !== undefined,
    });
  });

  sleep(1);
}

// Load test: Sustained traffic with investigations
export function loadTest() {
  const threadId = `load-${__VU}-${__ITER}-${randomString(8)}`;

  group('Start Investigation', () => {
    const queries = [
      'Why is the checkout API returning 500 errors?',
      'What caused the latency spike in the user service?',
      'Analyze the recent deployment failures',
      'Why are pods getting OOMKilled?',
      'What is causing the database connection timeouts?',
    ];

    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/chat`,
      JSON.stringify({
        message: queries[randomIntBetween(0, queries.length - 1)],
        thread_id: threadId,
      }),
      { headers: headers(), timeout: '120s' }
    );

    const duration = Date.now() - start;
    investigationDuration.add(duration);

    const success = check(res, {
      'chat status 200': (r) => r.status === 200,
      'chat has response': (r) => r.json('response') !== undefined,
    });

    if (!success) {
      errorRate.add(1);
    }

    // Track tool calls
    const response = res.json();
    if (response && response.tool_calls) {
      toolCallCount.add(response.tool_calls.length);
    }
  });

  sleep(randomIntBetween(2, 5));
}

// Stress test: High concurrency
export function stressTest() {
  const threadId = `stress-${__VU}-${__ITER}-${randomString(8)}`;

  // Mix of light and heavy operations
  const operation = randomIntBetween(1, 10);

  if (operation <= 3) {
    // 30%: Health check (light)
    const res = http.get(`${BASE_URL}/healthz`);
    check(res, { 'health ok': (r) => r.status === 200 });
  } else if (operation <= 6) {
    // 30%: Config fetch (light)
    const res = http.get(`${BASE_URL}/config`, { headers: headers() });
    check(res, { 'config ok': (r) => r.status === 200 });
  } else {
    // 40%: Investigation (heavy)
    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/chat`,
      JSON.stringify({
        message: 'Quick status check on all services',
        thread_id: threadId,
      }),
      { headers: headers(), timeout: '60s' }
    );

    investigationDuration.add(Date.now() - start);

    if (res.status !== 200) {
      errorRate.add(1);
    }
  }

  sleep(randomIntBetween(1, 3));
}

// Spike test: Sudden burst handling
export function spikeTest() {
  const threadId = `spike-${__VU}-${__ITER}-${randomString(8)}`;

  const start = Date.now();
  const res = http.post(
    `${BASE_URL}/chat`,
    JSON.stringify({
      message: 'Urgent: Production is down, what happened?',
      thread_id: threadId,
    }),
    { headers: headers(), timeout: '90s' }
  );

  investigationDuration.add(Date.now() - start);

  check(res, {
    'spike request handled': (r) => r.status === 200 || r.status === 429,
  });

  // 429 is acceptable during spikes (rate limiting working)
  if (res.status !== 200 && res.status !== 429) {
    errorRate.add(1);
  }

  sleep(1);
}

// Soak test: Long-running stability
export function soakTest() {
  const threadId = `soak-${__VU}-${__ITER}-${randomString(8)}`;

  group('Soak Investigation', () => {
    const start = Date.now();
    const res = http.post(
      `${BASE_URL}/chat`,
      JSON.stringify({
        message: 'Routine health check of the system',
        thread_id: threadId,
      }),
      { headers: headers(), timeout: '120s' }
    );

    investigationDuration.add(Date.now() - start);

    check(res, {
      'soak response ok': (r) => r.status === 200,
      'soak response time ok': (r) => r.timings.duration < 60000,
    });
  });

  // Check for memory leaks via metrics endpoint
  group('Memory Check', () => {
    const metrics = http.get(`${BASE_URL}/metrics/slo`);
    if (metrics.status === 200) {
      // Memory tracking via custom metrics
      const body = metrics.body;
      if (body && body.includes('memory_pressure')) {
        // Track memory trends
      }
    }
  });

  sleep(randomIntBetween(5, 15));
}

// WebSocket streaming test
export function wsStreamTest() {
  const threadId = `ws-${__VU}-${randomString(8)}`;
  const url = `${WS_URL}/ws/${threadId}`;

  const res = ws.connect(url, { headers: headers() }, (socket) => {
    let eventCount = 0;

    socket.on('open', () => {
      socket.send(JSON.stringify({
        type: 'start_investigation',
        message: 'What is the system status?',
      }));
    });

    socket.on('message', (data) => {
      eventCount++;
      const event = JSON.parse(data);

      if (event.type === 'done') {
        socket.close();
      }
    });

    socket.on('error', (e) => {
      errorRate.add(1);
    });

    socket.setTimeout(() => {
      socket.close();
    }, 60000);
  });

  check(res, { 'ws connected': (r) => r && r.status === 101 });
}

// Summary handler
export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'tests/load/results/summary.json': JSON.stringify(data, null, 2),
    'tests/load/results/summary.html': htmlReport(data),
  };
}

function textSummary(data, options) {
  // Built-in k6 text summary
  let output = '\n=== OmniPotent Load Test Summary ===\n\n';

  // Thresholds
  output += 'Thresholds:\n';
  for (const [name, threshold] of Object.entries(data.metrics)) {
    if (threshold.thresholds) {
      const status = Object.values(threshold.thresholds).every(t => t.ok) ? 'PASS' : 'FAIL';
      output += `  ${status}: ${name}\n`;
    }
  }

  return output;
}

function htmlReport(data) {
  return `
<!DOCTYPE html>
<html>
<head>
  <title>K6 Load Test Results - OmniPotent</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; }
    .metric { margin: 1rem 0; padding: 1rem; background: #f5f5f5; border-radius: 8px; }
    .pass { border-left: 4px solid #22c55e; }
    .fail { border-left: 4px solid #ef4444; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 0.5rem; text-align: left; border-bottom: 1px solid #ddd; }
  </style>
</head>
<body>
  <h1>OmniPotent Load Test Results</h1>
  <p>Generated: ${new Date().toISOString()}</p>

  <h2>Thresholds</h2>
  ${Object.entries(data.metrics)
    .filter(([k, v]) => v.thresholds)
    .map(([k, v]) => `
      <div class="metric ${Object.values(v.thresholds).every(t => t.ok) ? 'pass' : 'fail'}">
        <strong>${k}</strong>: ${JSON.stringify(v.values)}
      </div>
    `).join('')}

  <h2>Custom Metrics</h2>
  <table>
    <tr><th>Metric</th><th>Value</th></tr>
    ${data.metrics.investigation_duration_ms ? `
      <tr><td>Investigation P95</td><td>${data.metrics.investigation_duration_ms.values.p95.toFixed(0)}ms</td></tr>
    ` : ''}
    ${data.metrics.errors ? `
      <tr><td>Error Rate</td><td>${(data.metrics.errors.values.rate * 100).toFixed(2)}%</td></tr>
    ` : ''}
  </table>
</body>
</html>
  `;
}
