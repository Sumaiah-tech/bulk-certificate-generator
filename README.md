# Bulk Certificate Generator

A backend API that takes a list of recipients in **one request**, validates them, generates a PDF certificate for every valid recipient **in the background**, lets you track progress, and lets you download the results (one by one, or all at once as a ZIP).

**Stack:** Python 3.10+ · FastAPI · PostgreSQL (SQLAlchemy 2) · Celery + Redis · ReportLab · pytest

> New to the code? Read **[WALKTHROUGH.md](WALKTHROUGH.md)**. It traces one certificate through the whole system and answers the questions a reviewer is likely to ask.

---

## How it works (30-second version)

```
 client ──POST /jobs──▶  FastAPI  ──validate + save──▶  PostgreSQL
                           │  (returns 202 + job id immediately)
                           └──enqueue dispatch_job──▶  Redis ──▶ Celery worker(s)
                                                                   │ one task per certificate
                                                                   ▼
                                    PDFs on disk  ◀── ReportLab ◀──┘   status → PostgreSQL
 client ──GET /jobs/{id}──▶ progress   ·   GET /jobs/{id}/download ──▶ ZIP of all PDFs
```

| Job status | Meaning |
|---|---|
| `PENDING` | accepted, no certificate started yet |
| `PROCESSING` | at least one certificate started |
| `COMPLETED` | every recipient got a certificate |
| `COMPLETED_WITH_ERRORS` | some succeeded, some were invalid / duplicate / failed |
| `FAILED` | nothing could be generated |

| Certificate status | Meaning |
|---|---|
| `PENDING` / `PROCESSING` | waiting / being generated |
| `COMPLETED` | PDF ready |
| `FAILED` | valid recipient, but generation failed (reason in `error`) |
| `INVALID` | recipient data rejected (reason in `error`) |
| `DUPLICATE` | same email already appeared earlier in this job |

---

## Setup & run

You need **Python 3.10+**. Docker Desktop is needed for Options A and B, but **not** for Option C.
Run every command in a terminal **opened in the project folder** (PowerShell on Windows; on macOS/Linux use `python3` instead of `py`, and `source .venv/bin/activate`).

### Option A: everything in Docker (production-like, simplest)

```powershell
docker compose up --build
```

Starts PostgreSQL, Redis, the API (port 8000) and a Celery worker. Open **http://localhost:8000/docs**, click *Authorize*, enter the API key (default `dev-secret-key`).
Stop with `Ctrl+C`; `docker compose down` removes the containers (`-v` also deletes the data).

### Option B: development mode (API + worker on your machine, DB + Redis in Docker)

```powershell
docker compose up -d db redis

py -m venv .venv
.\.venv\Scripts\Activate.ps1          # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt
copy .env.example .env
```

Then in **two separate PowerShell windows** (activate the venv in each):

```powershell
# window 1: the API
uvicorn app.main:app --reload

# window 2: the worker (Windows can't use Celery's default "prefork" pool, so use threads)
celery -A app.celery_app.celery_app worker --loglevel=info --pool=threads --concurrency=4
```

If you forget the worker, jobs are accepted but stay `PENDING`.

### Option C: "lite mode" (no Docker, no Redis, no Postgres)

For a quick look. Uses a SQLite file, and Celery runs tasks **inline** inside the API process (so `POST /jobs` returns after all certificates are done: fine for demos, not for production).

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DATABASE_URL = "sqlite:///./lite.db"
$env:CELERY_EAGER = "true"
uvicorn app.main:app
```

### Run the tests

```powershell
pip install -r requirements-dev.txt
pytest
```

**57 tests**, no Docker/Redis/Postgres needed: a throw-away SQLite file and inline Celery, forced by `tests/conftest.py`, so tests can never touch your real database.

### Try it end to end

With the server running (Option A or B), in a second terminal:

```powershell
python scripts/demo.py 500
```

It submits 500 recipients, prints live progress, and saves `certificates.zip`.

---

## Using the API

All endpoints except `/health` and the QR verification endpoint need the header `X-API-Key: <key>` (set `API_KEYS` in `.env`; comma-separate several). In PowerShell use `curl.exe` (plain `curl` is an alias for something else), or just use http://localhost:8000/docs.

### 1. Submit a job: `POST /api/v1/jobs`

```powershell
curl.exe -X POST http://localhost:8000/api/v1/jobs `
  -H "X-API-Key: dev-secret-key" -H "Content-Type: application/json" `
  -d "@examples/request.json"
```

```json
{
  "event_name": "Python Bootcamp 2026",
  "issuer": "DSCE Coding Club",
  "issue_date": "2026-10-08",
  "recipients": [
    { "name": "Ada Lovelace", "email": "ada@example.com" },
    { "name": "Not An Email", "email": "oops" }
  ]
}
```

Returns **202 Accepted** immediately (plus a `Location` header):

```json
{
  "job_id": "91cf14e4-8a79-442e-b671-db5b2ab9701f",
  "status": "PENDING",
  "progress": { "total": 2, "completed": 0, "failed": 0, "invalid": 1, "duplicate": 0,
                "pending": 1, "processing": 0, "percent": 50.0 },
  "certificates_url": "/api/v1/jobs/91cf14e4-.../certificates",
  "archive_url": null
}
```

Bigger test: `python scripts/make_sample_request.py 500 > big.json`, then post `big.json` (every 50th recipient is deliberately invalid).

### 2. Check progress: `GET /api/v1/jobs/{job_id}`

Poll it. When `status` is `COMPLETED`, `COMPLETED_WITH_ERRORS` or `FAILED` the job is finished, and `archive_url` is set if at least one certificate was generated.

### 3. See what happened to each recipient: `GET /api/v1/jobs/{job_id}/certificates`

Paginated (`limit` up to 500, `offset`) and filterable: `?status=FAILED`, `?status=INVALID`, ...
`index` is the 0-based position of the recipient in your request.

```json
{ "total": 4, "limit": 100, "offset": 0, "items": [
  { "certificate_id": "53547be9-...", "index": 0, "recipient_name": "Ada Lovelace",
    "recipient_email": "ada@example.com", "status": "COMPLETED", "error": null,
    "attempts": 1, "download_url": "/api/v1/certificates/53547be9-.../download" },
  { "index": 3, "status": "INVALID", "error": "email is invalid (An email address must have an @-sign.)", "...": "..." },
  { "index": 4, "status": "DUPLICATE", "error": "duplicate email (first seen at index 0)", "...": "..." }
]}
```

### 4. Retrieve certificates

| Endpoint | Returns |
|---|---|
| `GET /api/v1/certificates/{certificate_id}/download` | one PDF |
| `GET /api/v1/jobs/{job_id}/download` | ZIP of all PDFs + `manifest.csv` listing **every** recipient with status and error |

```powershell
curl.exe -H "X-API-Key: dev-secret-key" -o certificates.zip http://localhost:8000/api/v1/jobs/<job_id>/download
```

### 5. Verify a certificate (public): `GET /api/v1/certificates/{certificate_id}/verify`

The URL inside each certificate's QR code. No API key. Returns name, event, issuer and date, **never the email**. Set `PUBLIC_BASE_URL` to an address people can actually reach; it is baked into the QR code.

### Errors

`401` bad/missing key · `404` unknown id · `409` not ready (job still running / certificate not generated) · `422` invalid request · `503` task queue unreachable (the job is marked `FAILED`, its id is returned).

---

## Design decisions

**Background processing with Celery + Redis (not synchronous).** A request may hold thousands of recipients; generating them inside the HTTP request would time out, block a web worker and lose everything on a dropped connection. So `POST /jobs` only validates and stores, then returns `202`. Workers do the heavy lifting and scale by adding more of them. FastAPI's `BackgroundTasks` was the lighter alternative, but it dies with the API process and can't spread across machines. Trade-off: two extra moving parts (Redis, worker), which Docker Compose hides, and lite mode (Option C) removes entirely for demos.

**One Celery task per certificate.** A failure affects exactly one certificate, certificates generate in parallel, and progress is granular. A small `dispatch_job` task does the fan-out so the HTTP request stays fast even for 5,000 recipients.

**Bad rows don't reject the request.** Request-level problems (no recipients, missing `event_name`, bad date) return `422`. Problems with *individual* recipients (bad email, missing name, duplicate) are stored as `INVALID` / `DUPLICATE` with a reason, and everyone else is still generated. One typo in a 2,000-row list never forces a full resubmit.

**Retries only for transient errors.** I/O errors (`OSError`) retry with exponential backoff (2s, 4s, 8s). Anything else fails immediately: retrying a deterministic bug just wastes time. Error messages shown to clients are only the exception class, never a message that might contain a server path.

**Idempotent, crash-safe tasks.** Workers acknowledge a task *after* finishing it (`acks_late`), so if a worker dies the task is redelivered. That is safe because (a) a task first *claims* its certificate with one atomic `UPDATE ... WHERE status = 'PENDING'`, so re-running a finished certificate is a no-op, and (b) PDFs are written to a temp file then atomically renamed, so a half-written PDF can never appear. If a worker dies mid-certificate, the certificate sits in `PROCESSING`; after `PROCESSING_TIMEOUT_SECONDS` the claim is considered stale and the redelivered task takes over, so nothing stays stuck forever. (Redis' redelivery delay is set to twice that timeout so the order of events always works out.)

**Exactly-once job completion.** After each certificate ends, its task checks whether it was the last. A row lock on the job (`SELECT ... FOR UPDATE`) makes concurrent workers take turns, so the final status and ZIP are produced once. If building the ZIP fails, the job still gets its status and the download endpoint rebuilds the ZIP on demand.

**PDFs on disk, paths in the database** (relative paths, with a check that nothing escapes the storage folder). Simple and enough here. API and worker share one volume; for several machines, swap `services/storage.py` for S3/MinIO.

**Unique certificate ID + QR verification.** Every certificate has a UUID printed on it and encoded in a QR code that opens the public verify endpoint.

**Small hardening details:** constant-time API-key comparison that is safe against non-ASCII headers; file names built from recipient names are sanitised; `manifest.csv` neutralises spreadsheet formula injection (`=...`) and has a UTF-8 BOM so Excel reads it; the date formatter avoids `%-d` (crashes on Windows); long names shrink to fit the page; the container runs as a non-root user.

**Tests use SQLite + inline Celery.** Fast, no infrastructure. The one PostgreSQL-specific behaviour (the row lock) is ignored by SQLite, so the unit tests do not exercise it. It was verified separately by running the real stack (PostgreSQL 16 + Redis + a 4-process Celery worker) with several jobs submitted at the same time: every job finalised exactly once with the right PDF count.

### Known limitations / future work

- **Latin characters only.** ReportLab's built-in fonts can't draw Kannada, Hindi or Chinese names; such recipients are marked `INVALID` with a clear reason instead of producing a broken PDF. Fix: bundle a Unicode font (Noto Sans family) plus a text-shaping library for Indic scripts.
- Tables are created with `create_all` at startup; a real deployment should use Alembic migrations.
- No idempotency key on `POST /jobs` (a client retry creates a second job), no endpoint to re-run only failed certificates, no completion webhook, old files are never cleaned up.
- One shared API-key list (no per-client keys or rate limiting).

---

## Project layout

```
app/
  main.py              FastAPI app + startup
  api.py               routes
  schemas.py           request/response models
  models.py            Job + Certificate tables, status enums
  tasks.py             Celery tasks (dispatch_job, generate_certificate)
  celery_app.py        Celery configuration (with the reasons)
  security.py          API-key check
  config.py            settings from environment / .env
  database.py          engine + session
  services/
    validation.py      per-recipient validation + duplicate detection
    certificate_pdf.py the single predefined template (ReportLab + QR)
    generation.py      generate one certificate, close out a job   <- the core
    jobs.py            create job, progress counts
    archive.py         ZIP + manifest.csv
    storage.py         paths, atomic writes, safe file names
tests/                 pytest suite (57 tests)
examples/request.json  scripts/make_sample_request.py  scripts/demo.py
docker-compose.yml  Dockerfile  .env.example  WALKTHROUGH.md
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | SQLAlchemy URL |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker |
| `STORAGE_DIR` | `storage` | where PDFs/ZIPs are written |
| `API_KEYS` | `dev-secret-key` | comma-separated valid keys, **change it** |
| `PUBLIC_BASE_URL` | `http://localhost:8000` | base URL inside QR codes |
| `MAX_RECIPIENTS_PER_REQUEST` | `5000` | larger requests get a 422 |
| `MAX_RETRIES` / `RETRY_BACKOFF_SECONDS` | `3` / `2` | retry policy for transient errors |
| `PROCESSING_TIMEOUT_SECONDS` | `300` | after this, a `PROCESSING` certificate is considered orphaned |
| `CELERY_EAGER` | `false` | `true` = run tasks inline (lite mode / tests) |
