# Section 4: AI reflection

<!--
DRAFT. Clive: this was drafted from what actually happened in the build log,
so the facts are real, but it has to end up in your words. Rewrite anything that
doesn't sound like you, add what you catch in your own review of the code, and
fill in question 4 yourself. Delete this comment before submitting.
-->

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

The straightforward way to implement "once booked, that slot must not be available to others" is to query whether the slot is free, and insert if it is. <!-- TODO Clive: say honestly whether this is what you'd have written yourself. --> The AI pointed out that two requests arriving at the same moment can both pass that check, and that a lock in Python wouldn't help once there's more than one server. It suggested a PostgreSQL **exclusion constraint** over the time range, filtered to `status = 'booked'`, so the database itself rejects the second booking and cancelled appointments stop counting automatically.

I hadn't used exclusion constraints before, so before accepting it I ran the SQL by hand against a local Postgres: two back-to-back slots (should pass), the same slot for another doctor (pass), re-booking a cancelled slot (pass), and a 09:15 booking overlapping 09:00-09:30 (should fail). All four behaved as expected. The concurrency test (10 simultaneous bookings, exactly one succeeds) now proves it on every CI run.

A second one: the AI suggested having `/health` return the deployed commit SHA, so the pipeline can wait until the merged commit is actually live instead of just firing the deploy and hoping.

## 3. Where the AI was wrong or incomplete, and how it was caught

- **A test that passed for the wrong reason.** The first draft of the "no bookings within one hour" test booked 08:30 on a day when "now" was 08:00 and expected a 422. But 08:30 is also before the doctor starts work at 09:00, so the test would have passed even if the one-hour rule didn't exist. It was caught when the test file was re-read before committing, and replaced with two precise tests: one moves the clock to 08:45 and books 09:00 (inside working hours, only 15 minutes away, must fail with `BOOKING_TOO_SOON`); the other books exactly 60 minutes ahead (must succeed). Lesson for me: a test only proves something if it fails when the rule is removed.
- **Something the docs didn't confirm.** When the AI looked up Render's deploy-hook documentation, the page did not say whether deploy hooks still work with auto-deploy turned off. Rather than assume, it flagged this as unverified, and I confirmed it on the first real deploy. <!-- TODO Clive: confirm after first deploy and update this line. -->
- **Environment problems it couldn't solve by itself.** The sandbox it worked in couldn't reach PyPI, so it couldn't run the API there. Instead of claiming the code worked, it unit-tested the pure slot logic locally, checked the database constraint SQL against a real Postgres, and relied on GitHub Actions to run the full suite. The first CI run was the real test. <!-- TODO Clive: note here if the first CI run found anything, and what. -->
- <!-- TODO Clive: add at least one thing YOU found while reading the code yourself. This is the strongest possible answer to this question. -->

## 4. Two decisions I made without AI

<!--
TODO Clive: this must be genuinely yours. Candidates worth thinking about,
only if they are actually true:
- Something you changed in the code or README after reviewing it, against or
  beyond what the AI wrote.
- A scope call: e.g. deciding which bonus items or extras are worth the time,
  or deciding to submit early rather than use the full 5 days.
- Judgement from your own context (e.g. that SMS matters more than email for
  patients in Kenya, or which Render region to use) that you'd defend from
  experience.
For each: what you decided, and why you trusted your own judgement there.
-->
