# Section 4: AI reflection

I used Claude (Anthropic) as a pair programmer for this assessment, in a single long working session. I'm a backend developer early in my career, and I treated the AI the way I'd treat a fast senior colleague: useful for drafts and for spotting things I hadn't thought of, but everything it produced had to be something I could explain and defend in a follow-up interview.

## 1. What I used AI for, by section

**Section 1, system design**
- Talking through the brief and listing the ambiguities (lunch breaks, lead time, what "upcoming" means, whether reschedule can change doctor).
- Comparing "pre-generated slot rows" against "computed slots", and choosing how to guarantee no double booking.
- Drafting the README design section, which I then edited.

**Section 2, API**
- Generating most of the first draft of the code: models, migration, services, routes, error handling.
- Writing the test suite, including the concurrency tests.
- Checking lint and formatting with ruff.

**Section 3, deployment**
- Writing the Dockerfile, `docker-compose.yml`, `render.yaml` and the GitHub Actions workflow.
- Looking up Render's current documentation instead of relying on memory: whether `btree_gist` is supported, the current name of the auto-deploy setting (`autoDeployTrigger`, the older `autoDeploy` is deprecated), the deploy hook's `ref` parameter, the `RENDER_GIT_COMMIT` variable, and the free-tier limits.

**Section 4**
- A first draft of this file from the session log, which I rewrote.

## 2. An AI suggestion that improved the work

My prompt was the whole assessment PDF plus: *"Recommend what's best for this project to be the best... always go for the best option."*

The straightforward way to implement "once booked, that slot must not be available to others" is to query whether the slot is free, and insert if it is. The AI pointed out that two requests arriving at the same moment can both pass that check, and that a lock in Python wouldn't help once there's more than one server. It suggested a PostgreSQL **exclusion constraint** over the time range, filtered to `status = 'booked'`, so the database itself rejects the second booking and cancelled appointments stop counting automatically.

I hadn't used exclusion constraints before, so I didn't accept it on trust. Claude ran the constraint SQL against a local Postgres with four cases: two back-to-back slots (should pass), the same slot for another doctor (pass), re-booking a cancelled slot (pass), and a 09:15 booking overlapping 09:00-09:30 (should fail). All four behaved as expected, and I went through the results and the migration before relying on it. The concurrency test (10 simultaneous bookings, exactly one succeeds) now proves it on every CI run.

A second one: the AI suggested having `/health` return the deployed commit SHA, so the pipeline can wait until the merged commit is actually live instead of just firing the deploy and hoping.

## 3. Where the AI was wrong or incomplete, and how it was caught

- **A test that passed for the wrong reason.** The first draft of the "no bookings within one hour" test booked 08:30 on a day when "now" was 08:00 and expected a 422. But 08:30 is also before the doctor starts work at 09:00, so the test would have passed even if the one-hour rule didn't exist. It was caught when the test file was re-read before committing, and replaced with two precise tests: one moves the clock to 08:45 and books 09:00 (inside working hours, only 15 minutes away, must fail with `BOOKING_TOO_SOON`); the other books exactly 60 minutes ahead (must succeed). Lesson for me: a test only proves something if it fails when the rule is removed.
- **Something the docs didn't confirm.** When the AI looked up Render's deploy-hook documentation, the page did not say whether deploy hooks still work with auto-deploy turned off. Rather than assume, it flagged this as unverified. The first real pipeline run settled it: with auto-deploy off, the hook deployed the exact merged commit, and the job only passed once `/health` reported that commit live.
- **Environment problems it couldn't solve by itself.** The sandbox it worked in couldn't reach PyPI, so it couldn't run the API there. Instead of claiming the code worked, it unit-tested the pure slot logic locally, checked the database constraint SQL against a real Postgres, and relied on GitHub Actions to run the full suite. The first CI run was the real test, and it failed: after the test fixtures were moved one folder deeper, the path to the Alembic config still pointed at the old location, so every integration test errored at setup. It was a one-line fix, but it shows why "the AI said it works" isn't evidence.
- **A concurrency bug that two rounds of AI fixes didn't fully solve.** The race test (10 simultaneous bookings for one slot) passed on the first pull request, then failed on `main` after merging. PostgreSQL was occasionally resolving the race with a deadlock error instead of a constraint violation, and the API returned a 500 instead of a 409.
  - The AI's first fix was to retry on deadlock. CI passed, so it looked done.
  - Then the race test failed again on a pull request that only changed the README: the retries were deadlocking with each other.
  - The second fix queues conflicting writers with PostgreSQL advisory locks taken in a fixed order (doctor, then patient). It passed four CI runs in a row.

  What I took from it: one green run means little for a race condition. The test now races six slots per run, and before merging I re-ran CI several times.
- **Tests that passed without proving much.** In my own review I found that Claude's booking-horizon tests used a date so far beyond the 90-day limit that they would still pass if the rule were changed to anything from 1 to 133 days. Details are in section 4.

## 4. Two decisions I made without AI

**Technology stack.** I chose FastAPI, PostgreSQL and Render myself. At the start Claude listed options (FastAPI, Django REST Framework or Go; Render, Railway, Fly.io or a big cloud), and its recommendation happened to match what I picked, but I decided on a different basis: which stack I could explain line by line, debug under time pressure and deploy within the assessment window. These are tools I already know. I trusted my own judgement here because I'm the one who has to defend this code in a follow-up interview, and that matters more to me than any small advantage another stack might have had.

**Tightening the booking-horizon tests.** While reviewing the code I went through the three horizon tests (`test_beyond_booking_horizon_rejected`, `test_beyond_horizon_has_no_slots`, and the `BOOKING_TOO_FAR_AHEAD` case in the reschedule tests) and read them against `latest_bookable_day` in `scheduling.py` and the frozen clock in the test fixtures (Monday 19 October 2026). All three used 2 March 2027, which is 134 days after that "now". I ran `latest_bookable_day` with the horizon set to 1, 30, 90, 120 and 133 days, and that date was rejected every time. So the tests would still have passed if someone changed the 90-day rule to 120, or broke most of it. Nothing checked that the last allowed day is accepted and the first day after it is rejected. The rule itself (`local.date() > latest_day`) was correct; only the tests were loose. It's the same weakness this document describes for the one-hour test: a test only proves something if it fails when the rule changes. There was also a catch for the fix: with the clock frozen on 19 October, the cutoff falls on Sunday 17 January, when the test doctor doesn't work, so a boundary test has to move the clock to get a weekday cutoff. The boundary tests now book on the last allowed day (201) and are refused on the next one (422), for booking, availability and rescheduling. I worked out the problem and what the tests needed to do; Claude wrote the test code to that spec, and CI ran it.

In the same review I noticed a smaller inconsistency: "upcoming" appointments included one starting at exactly this moment (`>=`), while cancel and reschedule already treated it as started (`<=`). So for that one instant, an appointment showed as upcoming but couldn't be cancelled. "Upcoming" now means the start time is still in the future, and a test checks the exact start instant.

I trusted my own judgement here because it came from arithmetic I ran on the code, not just from reading it, and because CI going green, which is the AI's measure of success, couldn't have caught this. The tests were passing; they just weren't testing much.
