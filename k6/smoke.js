// k6 smoke: the happy path, a few times, against either the emulator or real AWS.
//
//   API_URL, ORGANISER_TOKEN, ATTENDEE_TOKEN come from the env file written by
//   `scripts/e2e.py smoke --k6-env-file ...` (make k6-smoke, deploy.yml).
//
// No remote jslib imports: the script runs offline-identical everywhere.
import http from 'k6/http';
import { check, fail } from 'k6';

const API_URL = (__ENV.API_URL || '').replace(/\/+$/, '');
const ORGANISER_TOKEN = __ENV.ORGANISER_TOKEN;
const ATTENDEE_TOKEN = __ENV.ATTENDEE_TOKEN;

if (!API_URL || !ORGANISER_TOKEN || !ATTENDEE_TOKEN) {
  throw new Error('API_URL, ORGANISER_TOKEN and ATTENDEE_TOKEN must be set');
}

export const options = {
  vus: 1,
  iterations: 3,
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
    checks: ['rate==1'],
  },
};

function headers(token, extra = {}) {
  return {
    headers: {
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
      ...extra,
    },
  };
}

export default function () {
  const startsAt = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString();
  const created = http.post(
    `${API_URL}/events`,
    JSON.stringify({ name: `k6 smoke ${__VU}-${__ITER}`, venue: 'k6 Hall', starts_at: startsAt, capacity: 5 }),
    headers(ORGANISER_TOKEN),
  );
  if (!check(created, { 'POST /events -> 201': (r) => r.status === 201 })) {
    fail(`create event failed: ${created.status} ${created.body}`);
  }
  const eventId = created.json('event_id');

  const event = http.get(`${API_URL}/events/${eventId}`, headers(ATTENDEE_TOKEN));
  check(event, {
    'GET /events/{id} -> 200': (r) => r.status === 200,
    'capacity is 5': (r) => r.json('capacity') === 5,
  });

  // Unique per VU/iteration/time; matches the API's [A-Za-z0-9_-]{8,128} rule.
  const key = `k6-smoke-${__VU}-${__ITER}-${Date.now()}`;
  const reserved = http.post(
    `${API_URL}/events/${eventId}/reservations`,
    JSON.stringify({ quantity: 1 }),
    headers(ATTENDEE_TOKEN, { 'idempotency-key': key }),
  );
  check(reserved, {
    'POST reservation -> 202': (r) => r.status === 202,
    'reservation is PENDING or CONFIRMED': (r) => ['PENDING', 'CONFIRMED'].includes(r.json('status')),
  });

  const reservation = http.get(
    `${API_URL}/reservations/${reserved.json('reservation_id')}`,
    headers(ATTENDEE_TOKEN),
  );
  check(reservation, { 'GET /reservations/{id} -> 200': (r) => r.status === 200 });
}
