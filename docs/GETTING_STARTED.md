# Getting started with TixLane

This guide is for anyone who has just found the repository and wants to know **what it is**,
**what they can try**, and **exactly which commands to run**. You don't need AWS experience
for the first two levels below. If you already know serverless AWS, the
[README](../README.md) and [ARCHITECTURE.md](ARCHITECTURE.md) are the faster read.

---

## 1. What TixLane is, in plain words

Imagine a concert with **10 seats**, and **50 people press "Book" at the same second**.
A naive system checks "are seats left?" for each person, sees "yes" 50 times, and sells
50 tickets. TixLane is a small ticket-booking backend that shows how to get this right, and
how to run it properly in the cloud.

What happens when you use it:

1. An **organiser** signs in and creates an event, for example "Synthwave Night, 10 seats".
2. An **attendee** signs in and reserves seats. The answer comes back immediately with the
   status **PENDING**. Seats are taken atomically, so the system can never sell more than
   it has.
3. A few seconds later a background worker turns the reservation into **CONFIRMED**, writes a
   ticket file (`tickets/<id>.json`) and sends a "ticket issued" notification.
4. If the attendee's app retries (say, the network dropped), it sends the same
   *Idempotency-Key* and gets the **same** reservation back, not a second one.
5. If a broken message ever gets stuck, it is retried 3 times, then parked in a "dead-letter
   queue", and an alarm fires so a human can look at it.

The booking itself is simple. The project exists to show the hard parts done properly:
no overselling, safe retries, background processing, security, testing, low cost, and
cleaning up after itself.

## 2. Words you'll see

| Term | Meaning here |
|---|---|
| **Serverless** | You write functions; AWS runs them on demand. No servers to manage, and you pay per request. |
| **Lambda** | An AWS function. TixLane has three: `api_events`, `api_reservations`, `worker_tickets`. |
| **API Gateway** | The public HTTPS front door that routes requests to the Lambdas. |
| **Cognito / JWT** | AWS's sign-in service. After signing in you get a *token* (JWT) that you send with each request. |
| **Groups** | `organisers` may create events; `attendees` may reserve. The token says which group you're in. |
| **DynamoDB** | AWS's NoSQL database. It holds events, reservations and idempotency records. |
| **SQS / DLQ** | A message queue between the API and the worker. The *dead-letter queue* (DLQ) holds messages that failed 3 times. |
| **SNS** | A notification topic: `ticket-issued` (business events) and `ops-alerts` (alarms). |
| **S3** | File storage. Ticket files live here and expire after 7 days. |
| **KMS** | Encryption keys. Everything stored is encrypted. |
| **Idempotency key** | A unique string the client sends, so repeating a request is safe. |
| **OpenTofu** | Infrastructure as code: text files (`infra/`) describe every AWS resource. An open-source fork of Terraform. |
| **Emulator** | A program that pretends to be AWS on your laptop (Floci, or MiniStack). No AWS account, no cost. |
| **Moto** | A Python library that fakes AWS inside unit tests. |
| **OIDC** | How GitHub Actions logs in to AWS without any stored password or key. |
| **CI** | GitHub Actions runs every check automatically on each push (`.github/workflows/ci.yml`). |

## 3. What you can do with it

| Level | What you get | Needs | Costs money? | Status today |
|---|---|---|---|---|
| **1. Tests & checks** | Run the 99 unit tests and every quality scanner; see the no-overselling proof | Python tools only | No | ✅ works (CI green for these) |
| **2. Full system on your machine** | The whole stack (API, sign-in, database, queue, worker) running on an AWS emulator | + Docker | No | ⚠️ not demonstrated yet (see §6) |
| **3. Pipeline in your fork** | Every check, including the emulator tests, run by GitHub on each push | A GitHub fork | No | ⚠️ integration jobs need two variables |
| **4. Real AWS deployment** | Deploy to your own AWS account for up to 48 h, then tear down | AWS account + care | Target $0 net (free-plan credits) | ⚠️ never run yet |

Most people only need Levels 1 and 2.

## 4. Install the tools

The `make` commands need a Unix shell: **Linux, macOS, or WSL2 on Windows** (Ubuntu is
fine). On plain Windows without WSL, use the "without make" commands given in each step.

| Tool | Version used | Needed for | Get it |
|---|---|---|---|
| Git | any recent | all | https://git-scm.com/downloads |
| Python | 3.13 (3.12 also works) | all | https://www.python.org/downloads/ (uv can also install it) |
| uv | 0.13.0 | all | `curl -LsSf https://astral.sh/uv/install.sh \| sh`, or https://docs.astral.sh/uv/ |
| make | any | convenience | `sudo apt install make` (Ubuntu/WSL); preinstalled on macOS |
| OpenTofu | 1.13.1 | Level 1 lint, Level 2+ | https://opentofu.org/docs/intro/install/ |
| tflint, Trivy, Checkov, gitleaks | see `versions.env` | `make scan` only | each project's release page (links in [NOTICE.md](../NOTICE.md)); Checkov: `uv tool install checkov==3.3.29` |
| Docker | Engine or Desktop | Level 2 | https://docs.docker.com/get-docker/ |
| k6, jq, curl | k6 2.3.0 | Level 2 load test / API by hand | https://grafana.com/docs/k6/latest/set-up/install-k6/ |

The exact pinned versions are in [`versions.env`](../versions.env). CI uses the same ones.

## 5. Level 1: run the tests and checks (no Docker, no AWS)

```bash
git clone https://github.com/teodor004/serverless-aws-tixlane.git
cd serverless-aws-tixlane

make install      # creates .venv from the committed uv.lock (same as: uv sync)
make test         # the unit tests      (without make: uv run pytest)
```

You should see something like:

```
99 passed in 40s
Required test coverage of 90.0% reached. Total coverage: 99.59%
```

Everything runs against **Moto**, a fake AWS inside the test process, so nothing leaves your
machine. The tests worth looking at first:

| Test file | What it proves |
|---|---|
| `tests/unit/test_concurrency.py` | 50 simultaneous reservations for 10 seats → exactly 10 succeed, 40 are told "sold out", and all 10 get confirmed tickets |
| `tests/unit/test_repository.py` | Sold-out attempts leave no partial data; retries with the same key return the original; conflicts are retried |
| `tests/unit/test_api_reservations.py` | HTTP behaviour: 202/400/403/404/409/422/503, replay header, queue failure heals on retry |
| `tests/unit/test_worker_tickets.py` | Worker confirms once, writes the ticket, notifies once, reports bad messages individually |
| `tests/unit/test_auth_config.py` | Group rules, token checks, and that the emulator-only auth shortcut is refused everywhere else |

**Try breaking it.** This shows the no-overselling test really works:

1. Open `src/tixlane/repository.py` and change
   `"attribute_exists(PK) AND remaining >= :q"` to `"attribute_exists(PK)"`.
2. Run `uv run pytest tests/unit/test_concurrency.py --no-cov`. It fails with
   `assert 50 == 10`: without the condition, all 50 people get a seat.
3. Undo the change: `git checkout -- src/tixlane/repository.py`.

More checks (each is also a CI job):

```bash
make lint      # ruff (style), mypy --strict (types), tofu fmt
               # without make: uv run ruff check . && uv run mypy
make scan      # tflint, Trivy, Checkov, gitleaks (needs those tools installed)
make verify    # every tool version and GitHub Action is pinned
make help      # list every target
```

## 6. Level 2: run the whole system on your machine (emulator)

> **Honest status:** this level is written and checked, but nobody has run it end-to-end
> yet (the review machine had no Docker). Expect some emulator differences on the first run.
> Each likely one has a switch, listed under "If something fails" below and in
> [EMULATOR_GAPS.md](EMULATOR_GAPS.md).

**Step 1: start Docker** and check it answers: `docker info`.

**Step 2: choose the emulator image.** Copy the example settings file:

```bash
cp .env.example .env
```

Open `.env` and set `FLOCI_IMAGE=` to the official Floci image, pinned to a version (find
it in the Floci project's documentation). This was deliberately left blank because the name
hadn't been verified (VERIFICATION_LOG V-001).

**Step 3: start the emulator.**

```bash
make up            # expect: "floci healthy at http://localhost:4566/_floci/health"
```

**Step 4: create the whole stack inside the emulator.**

```bash
make local-apply
```

This builds the Lambda package (`build/lambda.zip`) and creates every resource in the
emulator: tables, queues, functions, API, sign-in pool and alarms. It then plans again
and requires **"Second plan: no changes."**, which proves the configuration is stable. The
addresses of what was created are saved in `build/local-outputs.json`.

**Step 5: run the integration tests.**

```bash
make itest
```

These five tests sign in through the emulated Cognito, call the API over HTTP, wait for
CONFIRMED (≤ 30 s), read the ticket from storage, try to oversell over HTTP, and push a
broken message through to the DLQ and its alarm.

**Step 6: a guided demo.**

```bash
make smoke         # creates two test users, runs the full flow, prints a JSON result
```

It writes `build/evidence-local/smoke.json` (the result) and `build/k6-local.env`, which
holds the API address and two sign-in tokens (valid for 60 minutes).

**Step 7: try the API by hand** (needs `curl` and `jq`):

```bash
set -a; source build/k6-local.env; set +a        # loads API_URL, ORGANISER_TOKEN, ATTENDEE_TOKEN

# Organiser creates an event with 2 seats
EVENT_ID=$(curl -s -X POST "$API_URL/events" \
  -H "Authorization: Bearer $ORGANISER_TOKEN" -H "content-type: application/json" \
  -d '{"name":"Demo gig","venue":"Hall 1","starts_at":"2030-01-01T20:00:00Z","capacity":2}' | jq -r .event_id)

# Attendee reserves 1 seat -> 202, status PENDING
curl -si -X POST "$API_URL/events/$EVENT_ID/reservations" \
  -H "Authorization: Bearer $ATTENDEE_TOKEN" -H "content-type: application/json" \
  -H "Idempotency-Key: demo-key-0001" -d '{"quantity":1}'

# Send the exact same request again -> same reservation_id, plus header "Idempotent-Replayed: true"
# Use the same key with a different quantity -> 422 idempotency_key_reused
# Ask for more seats than are left -> 409 sold_out

# Check the status (copy reservation_id from the response above) -> PENDING, then CONFIRMED
curl -s "$API_URL/reservations/<reservation_id>" -H "Authorization: Bearer $ATTENDEE_TOKEN" | jq

# Try without a token -> 401;  an attendee creating an event -> 403
```

To read the ticket file from the emulated storage (dummy credentials, local endpoint only):

```bash
BUCKET=$(jq -r .bucket_name.value build/local-outputs.json)
AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=eu-central-1 \
  aws --endpoint-url http://localhost:4566 s3 cp "s3://$BUCKET/tickets/<reservation_id>.json" -
```

**Step 8: optional extras.**

```bash
make k6-smoke      # small load test (1 virtual user, 3 rounds), then deletes the test users
make dlq-drill     # sends a broken message, waits for the DLQ and the alarm, then empties the DLQ
```

**Step 9: clean up.**

```bash
make cleanup-users   # remove the test users created by `make smoke` (k6-smoke does this itself)
make local-destroy   # delete the stack from the emulator
make down            # stop the emulator container
```

**Cross-check on the second emulator (MiniStack):**

```bash
make down
rm -f infra/envs/local/terraform.tfstate*       # the old emulator's resources are gone with it
# set MINISTACK_IMAGE in .env
make up EMULATOR=ministack && make local-apply && make itest
```

### If something fails

| Symptom | Likely cause | What to do |
|---|---|---|
| `Set FLOCI_IMAGE in .env first` | `.env` missing or the image is blank | Step 2 |
| `floci not healthy ... after 120s` | Container didn't start, or the image name is wrong | `make logs`; check the image name; `EMULATOR_WAIT_SECONDS=300 make up` |
| Every API call returns **401** | The emulator doesn't check tokens the way AWS does (gap G-01) | `TF_VAR_auth_mode=emulator-unverified make local-apply` (allowed only in the local env) |
| `emulator mode, but https://...amazonaws.com is an AWS endpoint` | The emulator reports an AWS-style API address (G-03) | `TIXLANE_API_URL=<emulator's invoke URL> make itest` |
| Lambda fails with Python 3.13 errors | The emulator lacks the 3.13 runtime (G-10) | `PY_VERSION=3.12 make local-apply` |
| Functions can't reach the database or queue | Lambda containers can't see the emulator (G-11) | Check the emulator's Docker-network setting; see `lambda_aws_endpoint_url` in `infra/envs/local/variables.tf` |
| DLQ test fails waiting for the alarm | The emulator doesn't evaluate alarms (G-05) | `TIXLANE_EMULATOR_ALARMS=0 make itest` (checks the alarm definition instead) |
| `Second plan is NOT empty` | The emulator reports some setting differently | Read `build/second-plan.txt`; record it in EMULATOR_GAPS.md (G-16+) |
| `error: the following arguments are required: --outputs` | Calling `scripts/e2e.py` by hand with options after the subcommand | Global options first: `e2e.py --outputs ... --endpoint-url ... smoke` |
| `Permission denied` on `scripts/*.sh` | Checked out without the executable bit | `chmod +x scripts/*.sh` |

When you find a real gap, add a row to [EMULATOR_GAPS.md](EMULATOR_GAPS.md) with the switch you
used. That's how the project tracks emulator differences.

## 7. Level 3: run the pipeline in your own fork

1. Fork the repository on GitHub, then open your fork → **Actions** → enable workflows.
2. **Settings → Secrets and variables → Actions → Variables**: add `FLOCI_IMAGE` and
   `MINISTACK_IMAGE` (the same values as your `.env`).
3. Optional, for the cost estimate: add the secret `INFRACOST_API_KEY` (free key from
   infracost.io). Without it, that job skips cleanly.
4. Push any commit, or run the **ci** workflow manually.

What the jobs mean: **preflight** (versions and pins), **python** (tests), **iac**
(infrastructure checks and security scanners), **secrets** (leak scan), **integration**
(Level 2 on GitHub's machines; Floci gates, MiniStack only reports), **infracost** (cost
estimate). On the original repository today, the first five pass. The two integration jobs
stop at "Start emulator" until the image variables are set.

## 8. Level 4: deploy to your own AWS account (advanced)

Only do this if you are comfortable with AWS. It is designed to cost **$0 net** on a new
Free-plan account (credits cover the few cents of KMS keys), but it is still real
infrastructure that you must remove afterwards.

In outline (full detail in the [README "Real-deploy story"](../README.md#real-deploy-story-p6p7)
and [TEARDOWN.md](TEARDOWN.md)):

1. **Protect the account first:** MFA on the root user, an admin user, a $1 budget with
   alerts, and Free Tier alerts.
2. **Point the trust at your fork.** Copy `bootstrap/terraform.tfvars.example` to
   `bootstrap/bootstrap.auto.tfvars` (git-ignored) and set your `aws_account_id`,
   `github_owner` and `github_repo`. The deploy role trusts **only**
   `repo:<github_owner>/<github_repo>:environment:production`.
3. With your own admin credentials, run `tofu -chdir=bootstrap init` and
   `tofu -chdir=bootstrap apply`. This one-off setup creates the state bucket, an encryption
   key, the GitHub login trust and the deploy role.
4. In your fork: create the environment **production**, add yourself as **required
   reviewer**, and add the variables listed in the README.
5. Run **deploy** from the Actions tab. You approve twice: once to create the plan, and again
   to apply exactly that plan after reading it. Smoke and load tests run automatically.
6. Within 48 hours, run **destroy**: type `destroy tixlane-prod` and approve. The final
   "tag sweep" must report 0 leftover resources. Then follow TEARDOWN.md to remove the
   bootstrap.

Nothing in this repository can change your AWS account from your laptop by accident: local
commands only target the emulator, and real changes go only through those approved workflows.

## 9. Reading the code: a short tour

Follow one reservation through the code, in this order:

| Step | File | What to look for |
|---|---|---|
| 1 | `src/tixlane/models.py` | The shapes of requests and stored data, and their validation rules |
| 2 | `src/tixlane/handlers/api_reservations.py` | The HTTP entry point: who may call it, header and body parsing |
| 3 | `src/tixlane/services.py` → `ReservationService.reserve` | Builds the reservation, then queues it for the worker |
| 4 | `src/tixlane/repository.py` → `create_reservation` | **The key part:** one database transaction takes the seats, saves the reservation and records the idempotency key, all or nothing |
| 5 | `src/tixlane/handlers/worker_tickets.py` + `services.py` → `TicketIssuer` | The worker: write the ticket, mark CONFIRMED, notify. Every step is safe to repeat |
| 6 | `infra/modules/*/main.tf` | How each AWS piece is declared (start with `data` and `compute`) |
| 7 | `.github/workflows/deploy.yml` | How a reviewed plan becomes real infrastructure |

The *why* behind each design choice is in the short decision records:
[docs/adr/](adr/README.md).

## 10. FAQ

**Do I need an AWS account?** No, not for Levels 1–3. Only Level 4 uses AWS.

**Can running this cost me money?** Levels 1–3 never talk to AWS. Level 4 targets $0 net on
a Free-plan account, but you must destroy it within 48 hours. See [COST.md](COST.md).

**Could a local command accidentally change my AWS account?** The local setup uses fake
credentials and emulator-only addresses, and refuses any other account. The test scripts
refuse real AWS outside GitHub Actions. Real deployment only happens through workflows that
need your approval.

**Why are the integration jobs red on GitHub?** The emulator image names aren't set yet (see
§7). That's a configuration step, not a code failure.

**What's the `.claude/` folder?** Safety rules for the Claude Code AI assistant used to
build the project. They block it from changing real AWS or committing secrets. They don't
affect you unless you use Claude Code in this repository.

**Can I reuse this for my own project?** Yes. The patterns (conditional transaction,
idempotency, safe async worker, OIDC deploys, cost-aware design) carry over to most
serverless APIs. See [NOTICE.md](../NOTICE.md) for the clean-room statement and sources.

## 11. Where to go next

| If you want… | Read |
|---|---|
| The full technical picture | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Why each decision was made | [adr/](adr/README.md) |
| Security model and threats | [SECURITY.md](SECURITY.md), [THREAT_MODEL.md](THREAT_MODEL.md) |
| Cost details | [COST.md](COST.md) |
| What has been verified, and how | [VERIFICATION_LOG.md](VERIFICATION_LOG.md) |
| Emulator differences | [EMULATOR_GAPS.md](EMULATOR_GAPS.md) |
| Tearing down a deployment | [TEARDOWN.md](TEARDOWN.md) |
