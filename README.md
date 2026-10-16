# Clinic Booking API

A REST API that lets patients of a five-doctor clinic see free 30-minute slots, book one, cancel it, or move it. Built with FastAPI and PostgreSQL, deployed on Render, shipped through GitHub Actions.

| | |
|---|---|
| **Live API** | https://clinic-booking-api-tbw3.onrender.com (interactive docs at [`/docs`](https://clinic-booking-api-tbw3.onrender.com/docs)) |
| **Repository** | https://github.com/clivemisiko/clinic-booking-api |
| **Deploy branch** | `main` (merging a PR into `main` deploys it) |
| **AI reflection** | [docs/AI_REFLECTION.md](docs/AI_REFLECTION.md) |

> The service runs on Render's free plan, which sleeps after 15 minutes without traffic. The first request after a quiet spell can take about a minute while it wakes up. After that it responds normally.

## Contents

1. [Try it in two minutes](#try-it-in-two-minutes)
2. [Section 1: system design](#section-1-system-design)
3. [Section 2: API](#section-2-api)
4. [Testing](#testing)
5. [Running locally](#running-locally)
6. [Section 3: deployment and CI/CD](#section-3-deployment-and-cicd)
7. [Project layout](#project-layout)
8. [Section 4: AI reflection](#section-4-ai-reflection)

## Try it in two minutes

The five doctors are seeded on deploy. Register a patient, look at a doctor's free slots, then book one. Dr. Otieno (id 1) works Monday to Friday, so set `DAY` to an upcoming weekday.

```bash
API=https://clinic-booking-api-tbw3.onrender.com
DAY=2026-10-27   # any upcoming Monday-Friday

# 1. Register a patient (emails are unique, so this one is randomised); note the "id"
curl -s -X POST $API/patients -H 'Content-Type: application/json' \
  -d "{\"full_name\": \"Amina Wanjiku\", \"email\": \"amina+$RANDOM@example.com\"}"
PATIENT_ID=...   # the id from the response

# 2. Free slots for doctor 1 that day; pick a start_at from the list
curl -s "$API/doctors/1/availability?date=$DAY"
START=...        # e.g. "2026-10-27T10:00:00+03:00"

# 3. Book it: 201
curl -s -X POST $API/appointments -H 'Content-Type: application/json' \
  -d "{\"doctor_id\": 1, \"patient_id\": $PATIENT_ID, \"start_at\": \"$START\"}"

# 4. Run step 3 again: 409 SLOT_UNAVAILABLE
```

Or open `/docs` and do the same from the browser.

---

## Section 1: system design

### Reading the brief

The clinic's description boils down to a handful of hard rules, and one sentence that shapes the rest:

1. Each doctor has **set working hours**, divided into **30-minute slots**.
2. A patient picks a doctor and a day, sees the free slots, books one.
3. **Once booked, a slot must not be available to anyone else.** This is the rule that breaks first under real traffic, so most of the design is about making it hold under concurrent requests.
4. Patients can cancel (and, per the API section, reschedule). A cancelled slot becomes bookable again.
5. *"We're starting small but want to grow."* I took this as: keep it simple to run today, but don't make choices that have to be undone later (no in-memory locks, no single-server assumptions, no schema that only works for exactly 30 minutes).

### Architecture

```mermaid
flowchart LR
    client[Patient app / Swagger UI] -->|HTTPS JSON| api

    subgraph api[FastAPI service]
        direction TB
        routes[Routes<br/>HTTP in/out, status codes] --> services[Services<br/>booking rules, transactions]
        services --> domain[Domain<br/>pure slot arithmetic]
        services --> models[SQLAlchemy models]
    end

    models -->|asyncpg| db[(PostgreSQL<br/>exclusion constraints)]
```

Four layers, each with one job:

| Layer | Folder | Responsibility |
|---|---|---|
| Routes | `app/api/routes/` | Parse and validate HTTP input, call a service, pick the status code. No business logic. |
| Services | `app/services/` | The booking rules: validation order, transactions, row locks, turning database conflicts into domain errors. |
| Domain | `app/domain/slots.py` | Pure functions: generate a day's slots, check if a time is a valid slot. No database, no FastAPI, so it is unit-tested in milliseconds. |
| Data | `app/models/`, `alembic/` | Tables, constraints, migrations. |

Two pieces of infrastructure are injected rather than imported, so tests can swap them: the database engine, and a `Clock` (`app/core/clock.py`). Every rule involving "now" (no past bookings, the one-hour lead time, "upcoming" appointments) reads the clock, which is why the tests can freeze time at Monday 08:00 and be fully deterministic.

### Data model

```mermaid
erDiagram
    DOCTORS ||--o{ DOCTOR_WORKING_HOURS : "works"
    DOCTORS ||--o{ APPOINTMENTS : "sees"
    PATIENTS ||--o{ APPOINTMENTS : "books"
    APPOINTMENTS ||--o{ APPOINTMENT_EVENTS : "history"

    DOCTORS {
        int id PK
        string full_name
        string specialty
        bool is_active
    }
    DOCTOR_WORKING_HOURS {
        int id PK
        int doctor_id FK
        smallint weekday "0=Mon .. 6=Sun"
        time start_time "clinic-local"
        time end_time "clinic-local"
    }
    PATIENTS {
        int id PK
        string full_name
        string email UK
        string phone
    }
    APPOINTMENTS {
        uuid id PK
        int doctor_id FK
        int patient_id FK
        timestamptz start_at
        timestamptz end_at
        string status "booked | cancelled"
        text reason
        text cancellation_reason
        timestamptz cancelled_at
        int reschedule_count
    }
    APPOINTMENT_EVENTS {
        int id PK
        uuid appointment_id FK
        string event_type "booked | rescheduled | cancelled"
        timestamptz previous_start_at
        timestamptz new_start_at
        text note
    }
```

Notes on the model:

- **Working hours are a separate table, not columns on the doctor.** A doctor can have several windows per day, which is how a lunch break is modelled (09:00-12:30 and 13:30-17:00). It also covers part-time doctors and Saturday clinics without schema changes. The seed data uses all three patterns.
- **There is no `slots` table.** See the first decision below.
- **Appointment ids are UUIDs.** They show up in URLs and would end up in reminder emails. Sequential ids would let anyone walk through other patients' appointments by counting.
- **`appointment_events` is an append-only audit trail.** Rescheduling updates the appointment row in place, so the history of where it used to be lives here. In a healthcare setting "who changed this booking, and when" is a question someone will eventually ask.

### Key decisions and trade-offs

#### 1. Slots are computed, not stored

The two common designs:

| | Pre-generated `slots` rows | Compute slots from working hours (chosen) |
|---|---|---|
| Preventing double booking | Easy: mark a row as taken | Needs a database constraint over time ranges (decision 2) |
| Changing a doctor's hours | Regenerate future rows, and decide what to do with rows that already have bookings | Takes effect immediately |
| Storage | 5 doctors x 14 slots x ~260 working days ≈ 18k rows a year, growing with every doctor | Only actual appointments |
| Background job to create future slots | Required | Not needed |
| Variable lengths later (15 or 60 min) | Painful | Change one setting and the constraint still works |

Computing slots means the "is this slot free?" question needs a different guarantee, which is decision 2.

#### 2. Double booking is prevented by PostgreSQL, not by application code

The obvious implementation is *check if the slot is free, then insert*. Two requests arriving within a few milliseconds of each other both pass the check and both insert. A Python lock does not help either, because it only covers one process and the plan is to grow to several.

So the rule lives in the database as an **exclusion constraint**:

```sql
ALTER TABLE appointments
ADD CONSTRAINT ex_appointments_doctor_no_overlap
EXCLUDE USING gist (doctor_id WITH =, tstzrange(start_at, end_at, '[)') WITH &&)
WHERE (status = 'booked');
```

It reads: *no two booked appointments for the same doctor may have overlapping time ranges.*

- PostgreSQL enforces it atomically, so with 10 simultaneous requests for one slot, exactly one wins. `tests/integration/test_concurrency.py` fires those 10 requests on 10 separate connections and checks for one 201 and nine 409s.
- `WHERE status = 'booked'` is what makes cancellation work: a cancelled row stops counting, and the slot is free again without deleting anything.
- It checks **overlap**, not equal start times. A plain `UNIQUE (doctor_id, start_at)` would stop two 09:00 bookings but would happily accept a 09:00 and a 09:15. The constraint protects the data even from a bug in the validation code or a manual `INSERT`.
- The `'[)'` bounds make ranges half-open, so a 09:00-09:30 appointment does not collide with a 09:30-10:00 one.
- A second, identical constraint on `patient_id` stops one patient from holding two appointments at the same time with different doctors.

The service just inserts and translates the constraint violation into `409 SLOT_UNAVAILABLE`. There is deliberately no "check first" query: it would duplicate the constraint and still be racy.

What the first version got wrong, and how CI caught it: when several requests insert overlapping rows at the same instant, a transaction can end up waiting on another's uncommitted row while that one waits back, and PostgreSQL breaks the cycle by aborting one with a **deadlock error** (`40P01`) instead of a constraint violation. The data stays correct, but the patient got a 500 instead of a 409. The race test passed on the first pull request and then failed on `main`.

- **First fix:** retry the transaction on deadlock. That reduced it but didn't remove it: with ten requests retrying at once, the retries could deadlock again.
- **Second fix:** queue the writers that could collide before they write. Booking and rescheduling take two transaction-scoped advisory locks first: one on the doctor, then one on the patient. Two bookings can only conflict if they share a doctor or a patient, so conflicting writers now always go one at a time, and every transaction takes the locks in the same order, so the locks themselves can't deadlock. By the time a writer inserts, any competing booking has already committed and the exclusion constraint reports a clean `409`. The locks are released automatically at commit or rollback.
- **Safety net:** the retry stays (up to 5 attempts on `40P01`/`40001`) for anything unexpected.

The exclusion constraint is still the guarantee: the locks only decide the order conflicts arrive in. The cost is that bookings for the *same doctor* are processed one at a time, for a few milliseconds each, which is irrelevant at a clinic's scale. The concurrency test now races 10 patients for 6 different slots per run, so a lucky pass is much less likely to hide a regression.

Trade-off: this ties the project to PostgreSQL (exclusion constraints need the `btree_gist` extension). I'm comfortable with that. Postgres is available on every cloud I'd consider, Render supports the extension, and the alternative is weaker guarantees.

#### 3. Cancel and reschedule lock the appointment row

Two people (say the patient and a receptionist) cancelling the same appointment at once should produce one success and one "already cancelled", not two successes. Cancel and reschedule load the appointment with `SELECT ... FOR UPDATE`, so the second request waits for the first to commit and then sees the new status. Tested in `test_simultaneous_cancels_of_one_appointment_succeed_exactly_once`.

#### 4. Rescheduling updates the row in place

The alternative is cancel-and-rebook as two operations, which can fail halfway and leave the patient with no appointment at all. Updating `start_at`/`end_at` in one transaction swaps the old range for the new one atomically: if the new slot is taken, the constraint rejects the update and the original booking is untouched. The appointment keeps its id (so any link the patient has stays valid), `reschedule_count` goes up, and the move is recorded in `appointment_events`.

#### 5. One set of rules for availability, booking and rescheduling

`app/services/scheduling.py::validate_requested_slot` is the single definition of "a bookable slot". Booking and rescheduling call it, and availability filters with the same helpers. The result is that **every slot the availability endpoint shows will pass booking validation**, and there's a test that books every listed slot to prove it (`test_every_listed_slot_is_bookable`).

#### 6. Time zones

- All instants are stored as `timestamptz` (UTC on disk).
- Working hours are stored as local wall-clock times, because that's how a clinic thinks ("Dr. Otieno works 9 to 5").
- The clinic's zone is configuration: `CLINIC_TIMEZONE=Africa/Nairobi`.
- Responses always include the offset: `2026-10-20T09:00:00+03:00`.
- Requests may send any offset (`Z`, `+03:00`, ...). A time with **no** offset is read as clinic time, since that is almost certainly what a local patient means.

Nairobi has no daylight saving, but the code uses `zoneinfo` throughout, so a branch in a DST country works without changes.

#### 7. Status codes

| Code | Used when | Example |
|---|---|---|
| `201` | Appointment or patient created | |
| `404` | The doctor, patient or appointment doesn't exist | `DOCTOR_NOT_FOUND` |
| `409` | The request is fine but clashes with current state | `SLOT_UNAVAILABLE`, `APPOINTMENT_ALREADY_CANCELLED` |
| `422` | Malformed input, or input that breaks a booking rule | `VALIDATION_ERROR`, `SLOT_IN_PAST`, `OUTSIDE_WORKING_HOURS` |

The split between 409 and 422 is intentional. A **422** means "this request will never succeed as written, change it" (a Sunday slot, 09:15 instead of 09:00). A **409** means "this request was valid a moment ago, but the world changed" (someone took the slot), so a client could reasonably refresh availability and retry.

Every error has the same shape, with a stable machine-readable `code` so a frontend can branch on it without parsing English:

```json
{
  "error": {
    "code": "OUTSIDE_WORKING_HOURS",
    "message": "Requested time is outside Dr. Achieng Otieno's working hours on that day (09:00-12:30, 13:30-17:00)",
    "details": {
      "doctor_id": 1,
      "start_at": "2026-10-20T12:30:00+03:00",
      "working_hours": ["09:00-12:30", "13:30-17:00"]
    }
  }
}
```

### Ambiguities and the calls I made

The brief says to decide and document. These are the decisions:

| Question | Decision | Why |
|---|---|---|
| Is there authentication? | **No.** Requests carry `patient_id` directly. | Out of scope for the brief. In production every endpoint would sit behind auth and `patient_id` would come from the token, not the body. Listed under next steps. |
| How do patients come to exist? | `POST /patients` registers one (email is unique, case-insensitive). | Booking needs a real patient to attach to; email is the natural identity. |
| How do doctors and hours get in? | Seeded on deploy (`app/seed.py`, idempotent). No admin endpoints. | The brief fixes the clinic at five doctors. An admin API is easy to add later but isn't asked for. |
| Can a slot start at any minute? | **No.** Starts must sit on the 30-minute grid inside a working window (09:00, 09:30, ...). 09:15 gets `422 INVALID_SLOT_BOUNDARY`. | "Works in 30-minute slots" implies fixed slots, and the availability endpoint only ever offers grid times. |
| Lead time (bonus) | Bookings must start **at least 60 minutes** from now; exactly 60 is allowed. Configurable via `MIN_BOOKING_LEAD_MINUTES`. | Gives the clinic time to prepare. Availability hides slots inside this window too, so patients aren't shown slots they can't book. |
| How far ahead can people book? | **90 days**, configurable (`BOOKING_HORIZON_DAYS`). | Not in the brief, but without a limit someone can book 2035. Doctors' schedules also change; a horizon keeps bookings within the period the clinic can plan for. |
| Availability for a past date / day off / beyond horizon | `200` with an empty `slots` list. | "No free slots" is a correct answer, not an error. An unknown doctor is still `404`. |
| Can past or in-progress appointments be cancelled or rescheduled? | **No**, `422 APPOINTMENT_ALREADY_STARTED`. | Cancelling yesterday's appointment would rewrite history; that's a no-show, a different concept. |
| Can reschedule change the doctor? | **No**, same doctor only. | The brief says "move an appointment to a new slot". Changing doctor is closer to a new booking. |
| Rescheduling to the same slot | `422 SAME_SLOT` | Almost certainly a client bug; succeeding silently would hide it. |
| Is a cancellation reason required? | **Yes**, 3-500 characters, whitespace-only rejected. | The brief says "cancel with a reason". The reason is stored on the appointment and in the audit trail. |
| Can one patient hold two appointments at once? | **No**, `409 PATIENT_HAS_CONFLICTING_APPOINTMENT`. | They can't be in two consulting rooms at once. Enforced by a second exclusion constraint. |
| What does "upcoming" mean (bonus)? | Booked (not cancelled) appointments starting now or later, soonest first. | Cancelled ones aren't upcoming. |
| Inactive doctors | `is_active=false` doctors show no availability and reject bookings (`422 DOCTOR_INACTIVE`). Existing bookings stay. | Lets the clinic stop new bookings (e.g. leave) without deleting history. |

### How this grows

What changes as the clinic gets bigger, roughly in the order it would be needed:

1. **Multiple app instances.** Works as-is. The app holds no state between requests and the booking guarantee is in the database, so Render can scale the service horizontally.
2. **Authentication and roles** (patient, receptionist, admin), with `patient_id` taken from the token.
3. **Notifications** (SMS reminders matter more than email in Kenya). Write to an outbox table in the same transaction as the booking, and have a worker send them, so a booking is never lost because an SMS provider timed out.
4. **Schedule exceptions**: leave, public holidays, one-off clinics. A `doctor_time_off` table subtracted from the computed slots. This is where the computed-slots design pays off: no stored rows to rewrite.
5. **Multiple branches.** Add `clinic_id` to doctors and appointments, and move `CLINIC_TIMEZONE` from config into the clinic row.
6. **Read-heavy availability traffic.** Indexes on `(doctor_id, start_at)` already cover the availability query. If it ever becomes hot, cache per doctor-day with a short TTL, invalidated on booking. Not worth the complexity now.
7. **Idempotency keys** on `POST /appointments`, so a patient on a flaky mobile connection who taps "Book" twice gets one booking.

---

## Section 2: API

Full interactive reference: **`/docs`** (Swagger UI) or **`/redoc`** on the live URL.

| Method | Path | What it does | Success |
|---|---|---|---|
| `POST` | `/appointments` | Book a slot | `201` |
| `GET` | `/doctors/{id}/availability?date=YYYY-MM-DD` | Free 30-minute slots for a doctor on a date | `200` |
| `PATCH` | `/appointments/{id}/cancel` | Cancel with a reason; slot becomes bookable again | `200` |
| `PATCH` | `/appointments/{id}/reschedule` | Move to a new slot (validated like a fresh booking) | `200` |
| `GET` | `/patients/{id}/appointments` | **Bonus:** upcoming appointments, soonest first | `200` |
| `POST` | `/patients` | Register a patient | `201` |
| `GET` | `/patients/{id}` | Get a patient | `200` |
| `GET` | `/doctors` | List doctors with their weekly hours | `200` |
| `GET` | `/doctors/{id}` | Get a doctor | `200` |
| `GET` | `/appointments/{id}` | Get an appointment | `200` |
| `GET` | `/health` | Liveness, database check, and the deployed commit SHA | `200` / `503` |

Both bonus items are implemented: the upcoming-appointments endpoint and the one-hour minimum lead time.

### Booking validation, in order

`POST /appointments` (and the new slot in `/reschedule`) is checked in this order, and stops at the first failure:

| # | Check | Error |
|---|---|---|
| 1 | Doctor exists | `404 DOCTOR_NOT_FOUND` |
| 2 | Patient exists | `404 PATIENT_NOT_FOUND` |
| 3 | Doctor is active | `422 DOCTOR_INACTIVE` |
| 4 | Not in the past | `422 SLOT_IN_PAST` |
| 5 | At least 60 minutes from now | `422 BOOKING_TOO_SOON` (includes `earliest_allowed`) |
| 6 | Within the 90-day horizon | `422 BOOKING_TOO_FAR_AHEAD` |
| 7 | Inside the doctor's working hours that day | `422 OUTSIDE_WORKING_HOURS` (includes the day's hours) |
| 8 | On a 30-minute boundary | `422 INVALID_SLOT_BOUNDARY` |
| 9 | Slot not already taken | `409 SLOT_UNAVAILABLE` (enforced by the database) |
| 10 | Patient not booked elsewhere at that time | `409 PATIENT_HAS_CONFLICTING_APPOINTMENT` |

Rescheduling first checks that the appointment exists (`404`), isn't cancelled (`409 APPOINTMENT_CANCELLED`), hasn't started (`422`) and that the new time differs from the old one (`422 SAME_SLOT`), then runs the list above.

---

## Testing

```bash
pytest --cov
```

The suite has two parts:

- **Unit tests** (`tests/unit/`) for the pure slot engine: grid generation, lunch breaks, partial trailing slots, misaligned times, timezone handling. No database, run in well under a second.
- **Integration tests** (`tests/integration/`) that call the real API (via HTTPX's ASGI transport) against a **real PostgreSQL database** built by the real Alembic migrations. SQLite was not an option: the double-booking guarantee *is* a PostgreSQL feature, and testing against anything else would skip the most important behaviour.

Things the integration tests cover beyond the happy paths:

- **Race conditions:** 10 concurrent bookings for one slot (exactly one wins), 4 concurrent reschedules into one slot, 5 concurrent cancels of one appointment.
- **Time:** the clock is frozen at Monday 08:00 Nairobi time, so "past", "within an hour" and "upcoming" are deterministic. Tests move the clock forward where needed.
- **Consistency:** every slot listed by availability is booked successfully, one by one, until none remain.
- **Every error code** in the tables above, with its HTTP status.
- **Audit trail** contents after book, reschedule, cancel.

CI fails the build if coverage drops below 85%.

---

## Running locally

### With Docker (recommended)

```bash
docker compose up --build
```

Open http://localhost:8000/docs. Migrations and the doctor seed run automatically on start.

Run the tests against the same Postgres container:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

(`docker compose` also creates the `clinic_test` database the tests use by default.)

### Without Docker

You need Python 3.12+ and PostgreSQL 13+.

```bash
createdb clinic && createdb clinic_test
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # adjust DATABASE_URL if needed
alembic upgrade head
python -m app.seed
uvicorn app.main:app --reload
```

### Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/clinic` | `postgres://` URLs (as Render provides) are converted automatically |
| `TEST_DATABASE_URL` | `.../clinic_test` | Database the test suite **wipes and rebuilds**; never point it at real data |
| `CLINIC_TIMEZONE` | `Africa/Nairobi` | Zone for working hours and response timestamps |
| `SLOT_MINUTES` | `30` | Slot length |
| `MIN_BOOKING_LEAD_MINUTES` | `60` | Minimum notice for a booking (bonus rule) |
| `BOOKING_HORIZON_DAYS` | `90` | How far ahead bookings are accepted |

---

## Section 3: deployment and CI/CD

**Live URL:** https://clinic-booking-api-tbw3.onrender.com

**Hosting:** Render, one Docker web service plus a managed PostgreSQL 16 database, both described in [`render.yaml`](render.yaml) (Render Blueprint), Frankfurt region (closest Render region to Nairobi).

**Which branch deploys, and how:** merging a pull request into **`main`** deploys it. Render's own auto-deploy is switched **off** (`autoDeployTrigger: "off"`), so code only reaches production through the pipeline, and only after every check has passed on the merged commit.

```mermaid
flowchart LR
    pr[Pull request] --> lint[Lint<br/>ruff]
    pr --> test[Tests<br/>pytest + Postgres 16]
    pr --> docker[Docker<br/>image builds]
    lint & test & docker --> gate{All green?}
    gate -->|PR| merge[Ready to merge]
    merge -->|push to main| again[Same 3 checks<br/>on merged code]
    again --> hook[Trigger Render deploy<br/>of that exact SHA]
    hook --> wait[Poll /health until<br/>it reports that SHA]
    wait --> smoke[Smoke test<br/>live endpoints]
```

What the pipeline ([`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml)) does:

**On every pull request**
1. **Lint:** `ruff check` and `ruff format --check`.
2. **Test:** starts a PostgreSQL 16 service container, installs dependencies, runs the full suite with coverage (fails under 85%).
3. **Docker:** builds the production image, so a broken Dockerfile is caught before merge, not during deploy.

**On merge to `main`**, the same three jobs run on the merged code. If all pass, a fourth job:

4. **Deploys:** calls Render's deploy hook with `ref=<commit SHA>`, so Render builds exactly the commit that was tested (not whatever is newest by the time it gets there).
5. **Waits for it to be live:** `/health` returns the running commit (from Render's `RENDER_GIT_COMMIT`). The job polls until it matches the merged SHA, for up to 15 minutes. A green deploy job therefore means "the new code is serving traffic", not just "a deploy was requested".
6. **Smoke tests** the live service: health and database OK, doctors listed, availability endpoint responding.

On start, the container runs `alembic upgrade head`, then the idempotent doctor seed, then Uvicorn (`scripts/start.sh`). Render waits for `/health` to pass before routing traffic to the new version.

Secrets and settings used by the pipeline:

| Name | Kind | Value |
|---|---|---|
| `RENDER_DEPLOY_HOOK_URL` | Repository secret | Deploy hook from the Render service settings |
| `APP_URL` | Repository variable | `https://clinic-booking-api-tbw3.onrender.com` |

Branch protection on `main` requires the lint, test and Docker checks to pass before a PR can be merged.

**Known limits of the free tier:** the web service sleeps after 15 minutes idle (about a minute to wake), and Render's free PostgreSQL databases expire 30 days after creation. Both are fine for an assessment and are the first things to change (paid instance, paid database) for real use.

---

## Project layout

```
app/
  api/
    deps.py              # session, settings and clock dependencies
    routes/              # one module per resource: HTTP only
  core/
    config.py            # settings from environment variables
    clock.py             # injectable clock (frozen in tests)
    errors.py            # domain errors -> HTTP status + error envelope
  db/                    # engine/session setup, declarative base
  domain/slots.py        # pure slot arithmetic (no I/O)
  models/                # SQLAlchemy models
  schemas/               # Pydantic request/response models
  services/              # booking rules and transactions
  main.py                # application factory
  seed.py                # idempotent seed of the five doctors
alembic/versions/        # migrations, incl. the exclusion constraints
tests/
  unit/                  # slot engine
  integration/           # API + real PostgreSQL, incl. concurrency
scripts/start.sh         # migrate, seed, serve
Dockerfile, docker-compose.yml, render.yaml
.github/workflows/ci-cd.yml
```

---

## Section 4: AI reflection

See [docs/AI_REFLECTION.md](docs/AI_REFLECTION.md).
