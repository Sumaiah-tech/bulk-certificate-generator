# Walkthrough: understand it, explain it, change it

Read this once with the code open. About 20 minutes.

## 1. Follow ONE certificate (the whole system in 9 steps)

Request: Ada, `ada@example.com`, in a job of 3 recipients.

1. **`api.py: create_job`** receives the JSON. Pydantic (`schemas.py: JobCreate`) checks the request-level fields. Bad -> `422`.
2. **`services/validation.py`** checks each recipient. Ada is fine -> status `PENDING`. A bad email would become `INVALID` with a reason; the request is still accepted.
3. **`services/jobs.py: create_job`** inserts 1 `Job` row + 3 `Certificate` rows and **commits**.
4. **`api.py`** calls `dispatch_job.delay(job_id)`, which puts a message in Redis, and returns `202` right away. (Commit happens *before* enqueue, so a worker can never look for a job that isn't saved yet.)
5. **`tasks.py: dispatch_job`** (on a worker) loads the `PENDING` certificate ids and queues one `generate_certificate` task each.
6. **`tasks.py: generate_certificate`** calls `generation.process_certificate(id)`.
7. **`generation.py` STEP 1, claim:** one atomic `UPDATE ... WHERE id=? AND status='PENDING'` flips it to `PROCESSING`. If 0 rows changed, someone else has it or it's done -> return. The first claimer also flips the job to `PROCESSING`.
8. **STEP 2, render:** `certificate_pdf.py` draws the PDF (name, event, date, QR) and `storage.py` writes it atomically (temp file -> rename). Success -> `COMPLETED`. `OSError` -> back to `PENDING` and Celery retries with backoff. Any other exception -> `FAILED` with a safe message.
9. **STEP 3, finalize:** `finalize_job_if_done` locks the job row. If no certificate is `PENDING`/`PROCESSING`, it builds the ZIP, sets `COMPLETED` / `COMPLETED_WITH_ERRORS` / `FAILED`, and stamps `finished_at`. The client polling `GET /jobs/{id}` now sees it.

## 2. "Why does that exist?"

**Why Celery + Redis instead of doing it in the request?** Thousands of PDFs would time out the request and die with the connection. The API stays fast; workers scale horizontally. Rejected alternative: FastAPI `BackgroundTasks` (dies with the API process, can't use other machines).

**Why one task per certificate?** Isolation (one failure affects one), parallelism, granular progress.

**What is `acks_late`, and why is it safe?** Normally a task is acknowledged when it *starts*; if the worker dies, the task is lost. With `acks_late` it's acknowledged when it *ends*, so a crash means redelivery. Redelivery means a task can run twice, which is only OK because of the claim in step 7: the second run finds the certificate already done (or freshly claimed) and does nothing. **acks_late is only safe because the task is idempotent.**

**What if a worker dies halfway through a certificate?** The row stays `PROCESSING`. Redis redelivers the message after `visibility_timeout` (2x `PROCESSING_TIMEOUT_SECONDS`). The claim accepts a `PROCESSING` row whose `started_at` is older than `PROCESSING_TIMEOUT_SECONDS`, so the new worker takes over. Nothing is stuck forever. (Test: `test_stale_processing_certificate_is_recovered`.)

**What is the row lock for?** Two workers finish the last two certificates at the same moment. Without a lock both might see "all done" and both build the ZIP and set the status. `SELECT ... FOR UPDATE` makes them queue; the first finalizes and sets `finished_at`, the second sees it and leaves. SQLite ignores `FOR UPDATE`, so unit tests don't cover it; it was verified on real PostgreSQL with 4 jobs submitted concurrently.

**Why do some errors retry and others don't?** `OSError` (disk hiccup) is transient: trying again can work. A `ValueError` from bad data is deterministic: it would fail identically every time.

**Why don't bad rows cause a 422?** One typo in 2,000 rows shouldn't force a full resubmit. The response and `/certificates?status=INVALID` say exactly which rows to fix.

**Why is `RecipientIn` typed `Any`?** So a wrong type (`"name": 123`) becomes an `INVALID` row instead of a 422 for the whole request.

**Why files on disk, not in the DB?** Simple and fast; the DB holds only a relative path. For multiple machines swap `storage.py` for S3.

**Why store enums as strings?** Portable (SQLite + PostgreSQL) and no `ALTER TYPE` migrations when adding a status.

**Why are progress counts computed, not stored?** One `GROUP BY` over the certificate rows is always correct; a stored counter can drift when workers race.

## 3. Likely "modify it live" requests

| Ask | Where | What to do |
|---|---|---|
| Add a field to the certificate (e.g. `course`) | `schemas.py` (`JobCreate`) -> `models.py` (`Job`) -> `jobs.create_job` -> `generation.process_certificate` (pass it) -> `certificate_pdf.py` (draw it) | add the field at each step; recreate tables (`docker compose down -v`) |
| Change the design | `services/certificate_pdf.py` only | colours at the top, positions in `render_certificate` |
| Change retry count / backoff | `.env`: `MAX_RETRIES`, `RETRY_BACKOFF_SECONDS` | no code change |
| Reject a new kind of bad data | `services/validation.py` | add a check in `_check_name`/`_check_email` + a test |
| Add a filter / endpoint | `api.py` + `services/jobs.py` | follow `list_certificates` |
| Re-run failed certificates | new endpoint: set `FAILED` certs back to `PENDING`, clear the job's `finished_at`, call `dispatch_job.delay` | ~15 lines |
| Make everything synchronous | set `CELERY_EAGER=true` | tasks run inline |

## 4. Be upfront about the limits

Say them before they're found: Latin-only fonts (documented, fails cleanly), `create_all` instead of migrations, no idempotency key, no cleanup of old files, one shared API-key list. Knowing your own gaps reads as seniority.

## 5. Self-test (answer without looking)

1. Why is the job committed before `dispatch_job.delay`?
2. What stops a certificate being generated twice if a message is delivered twice?
3. What happens to a certificate if the worker is killed with `kill -9` mid-render?
4. Why does `finalize_job_if_done` re-check the counts *after* taking the lock?
5. Which exceptions are retried, and why only those?
