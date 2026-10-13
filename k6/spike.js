// k6 spike against real AWS (ops.yml scenario "spike"), bounded by the brief:
// at most 50 virtual users and at most 5 minutes. The bounds are enforced below, at module
// load, so an edited stage list fails before a single request is sent.
//
//   API_URL, ATTENDEE_TOKEN, EVENT_ID come from `scripts/e2e.py prepare-load`.
//
// Cost bound: the HTTP API stage throttles at 10 rps (burst 20) and each function has a
// reserved concurrency of 5, so at most ~10 reservations/s reach Lambda. Over 4.5 minutes that
// is < 3,000 reservations -> < 3,000 each of Lambda invocations, SQS messages, S3 PUTs and SNS
// publishes: far inside the free tiers / credits (docs/COST.md). Throttled requests return 429
// at the gateway and never reach Lambda.
import http from 'k6/http';
import { check, sleep } from 'k6';

const MAX_VUS = 50;
const MAX_SECONDS = 300;

const STAGES = [
  { duration: '30s', target: 10 },
  { duration: '1m', target: 50 },
  { duration: '2m', target: 50 },
  { duration: '1m', target: 0 },
];

function seconds(duration) {
  const match = /^(\d+)(s|m)$/.exec(duration);
  if (!match) {
    throw new Error(`unsupported duration "${duration}" (use Ns or Nm)`);
  }
  return Number(match[1]) * (match[2] === 'm' ? 60 : 1);
}

const peakVus = Math.max(...STAGES.map((stage) => stage.target));
const totalSeconds = STAGES.reduce((sum, stage) => sum + seconds(stage.duration), 0);
if (peakVus > MAX_VUS || totalSeconds > MAX_SECONDS) {
  throw new Error(`spike exceeds the brief: ${peakVus} VUs / ${totalSeconds}s (max ${MAX_VUS} / ${MAX_SECONDS}s)`);
}

const API_URL = (__ENV.API_URL || '').replace(/\/+$/, '');
const ATTENDEE_TOKEN = __ENV.ATTENDEE_TOKEN;
const EVENT_ID = __ENV.EVENT_ID;
if (!API_URL || !ATTENDEE_TOKEN || !EVENT_ID) {
  throw new Error('API_URL, ATTENDEE_TOKEN and EVENT_ID must be set');
}

// Stage throttling and reserved concurrency make 429s expected, and 409 (sold out) is a valid
// business answer; only other statuses count as failures.
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 409, 429));

export const options = {
  stages: STAGES,
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<2000'],
  },
};

export default function () {
  const key = `k6-spike-${__VU}-${__ITER}-${Date.now()}`;
  const response = http.post(
    `${API_URL}/events/${EVENT_ID}/reservations`,
    JSON.stringify({ quantity: 1 }),
    {
      headers: {
        authorization: `Bearer ${ATTENDEE_TOKEN}`,
        'content-type': 'application/json',
        'idempotency-key': key,
      },
      tags: { name: 'POST /events/{id}/reservations' },
    },
  );
  check(response, {
    'accepted, sold out or throttled': (r) => [202, 409, 429].includes(r.status),
  });
  sleep(1);
}
