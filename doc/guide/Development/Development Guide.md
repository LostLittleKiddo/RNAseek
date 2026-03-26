# RNAseek -- Development Guide

> Local development setup without Docker, running directly on the host machine.

---

## Prerequisites

| Component      | Version  | Purpose                                   |
| -------------- | -------- | ----------------------------------------- |
| Python         | 3.11+    | Application runtime                       |
| Miniconda3     | Latest   | Conda environment (R, bioinformatics CLI) |
| Redis          | 7+       | Celery broker and Channels layer          |
| SQLite         | Built-in | Development database (auto-created)       |

---

## 1. Clone and Set Up

```bash
git clone <repository-url> /home/ubuntu/apps/rnaseek
cd /home/ubuntu/apps/rnaseek
```

### 1.1 Conda Environment

```bash
conda env create -n rnaseek -f environment.yml
conda activate rnaseek
pip install -r requirements.txt
```

### 1.2 Environment File

Create a `.env` in the project root (optional for dev -- defaults are sufficient):

```
RNASEEK_ENV=development
MEDIA_ROOT=/home/ubuntu/apps/rnaseek/media
```

When `RNASEEK_ENV` is not set or set to `development`, Django uses:
- SQLite database (`db.sqlite3`)
- `DEBUG=True`
- No SSL enforcement
- Console logging at INFO level for the `pipeline` logger

---

## 2. Initialize the Database

```bash
python manage.py migrate
```

This creates `db.sqlite3` in the project root with all tables.

---

## 3. Start Services

### 3.1 Redis

Redis must be running for Celery and Django Channels:

```bash
redis-server
```

Or if installed via apt:

```bash
sudo systemctl start redis
```

### 3.2 Django Development Server

```bash
python manage.py runserver
# or use Daphne for WebSocket support:
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

The application is available at `http://localhost:8000`.

### 3.3 Celery Worker

In a separate terminal:

```bash
conda activate rnaseek
celery -A config worker --loglevel=debug --concurrency=2
```

### 3.4 Celery Beat (Optional)

Only needed if testing scheduled tasks:

```bash
celery -A config beat --loglevel=info
```

### 3.5 Eager Mode (No Worker)

For quick debugging without running a separate worker process, set `CELERY_EAGER=1`:

```bash
CELERY_EAGER=1 python manage.py runserver
```

Tasks execute synchronously in the request process. This blocks the HTTP response until the pipeline finishes.

---

## 4. Running Tests

### 4.1 Full Test Suite

```bash
python manage.py test test -v2
```

**Note:** The test runner creates a temporary database. If using PostgreSQL locally, the DB user needs `CREATEDB` permission:

```bash
sudo -u postgres psql -c "ALTER USER rnaseek CREATEDB;"
```

### 4.2 Specific Test File

```bash
python manage.py test test.test_tusd_hooks -v2
python manage.py test test.test_upload_api -v2
python manage.py test test.test_validators -v2
```

### 4.3 Single Test

```bash
python manage.py test test.test_tusd_hooks.TusdHookViewTests.test_post_finish_creates_file_asset -v2
```

---

## 5. Project Structure

```
rnaseek/
  config/           Django project settings, Celery app, ASGI/WSGI entrypoints
  pipeline/
    views/           HTTP views (api.py for REST, pages.py for templates)
    tasks/           Celery tasks (pipeline tracks, modules)
    stats/           Stage 2 statistics engine (DESeq2, plots)
    templates/       HTML templates
    static/          CSS, JavaScript
    migrations/      Database migrations
    management/      Management commands (purge_expired)
  test/              Test suite (12 files)
  doc/               Documentation
  nginx/             Nginx configuration
  systemd/           systemd service files
  scripts/           Utility scripts (benchmarking)
  media/             User uploads and pipeline outputs (gitignored)
```

---

## 6. Key Configuration

### 6.1 Django Settings

`config/settings.py` auto-detects the environment via `RNASEEK_ENV`:

| Setting               | Development           | Production          |
| --------------------- | --------------------- | ------------------- |
| `DEBUG`               | `True`                | `False`             |
| Database              | SQLite                | PostgreSQL          |
| `SECRET_KEY`          | Hardcoded insecure    | From `.env`         |
| SSL redirect          | Disabled              | Enabled (HSTS)      |
| Celery eager          | Configurable (`CELERY_EAGER=1`) | Always async |
| Logging               | INFO for `pipeline`   | INFO for `pipeline` |

### 6.2 URL Routes

| Path                    | View               | Purpose                  |
| ----------------------- | ------------------ | ------------------------ |
| `/`                     | HomeView           | Landing page             |
| `/tutorials/`           | TutorialsView      | File format guides       |
| `/workspaces/`          | WorkspacesView     | Active submissions list  |
| `/new/`                 | NewSubmissionView   | 5-step wizard           |
| `/processing/<uuid>/`   | ProcessingView     | Real-time progress       |
| `/hub/<uuid>/`          | CoreHubView        | Results and modules      |
| `/api/upload/chunk`     | ChunkUploadView    | Legacy chunked upload    |
| `/api/tusd-hooks/`      | TusdHookView       | tusd webhook handler     |
| `/api/pipeline/core`    | CorePipelineView   | Pipeline dispatch        |
| `/api/jobs/<uuid>/`     | JobStatusView      | Job status polling       |

---

## 7. Common Development Tasks

### Add a New Migration

```bash
python manage.py makemigrations pipeline
python manage.py migrate
```

### Generate Synthetic Test Data

The `debug_payload.py` module generates synthetic `result_payload` for testing the Core Hub UI without running the real pipeline:

```bash
python manage.py shell
>>> from pipeline.debug_payload import generate_debug_payload
>>> payload = generate_debug_payload()
```

### Collect Static Files

```bash
python manage.py collectstatic --noinput
```

### Check for Deployment Issues

```bash
python manage.py check --deploy
```
