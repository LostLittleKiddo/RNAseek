# RNAseek — Docker Development Guide

> **Purpose:** Run the full RNAseek stack locally with Docker during development.  
> **Audience:** Developers working on the codebase before pushing to production.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [How It Works](#2-how-it-works)
3. [Development Workflow](#3-development-workflow)
4. [Running Tests](#4-running-tests)
5. [Working Without Docker](#5-working-without-docker)
6. [Switching to Production](#6-switching-to-production)
7. [Common Tasks](#7-common-tasks)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Quick Start

```bash
# Clone the repo
git clone <your-repo-url> && cd rnaseek

# Start the full stack (web + worker + beat + redis)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

That's it. The application is running at **http://localhost:8000**.

### What's Running

| Service    | URL / Port            | What It Does                                   |
| ---------- | --------------------- | ---------------------------------------------- |
| **web**    | http://localhost:8000 | Daphne ASGI server (Django + WebSocket)        |
| **worker** | —                     | Celery worker (runs bioinformatics pipelines)  |
| **beat**   | —                     | Celery Beat scheduler (periodic cleanup tasks) |
| **redis**  | localhost:6379        | Message broker + channel layer                 |

---

## 2. How It Works

The dev override (`docker-compose.dev.yml`) layers on top of the production compose file:

| Setting                | Production (`docker-compose.yml`) | Development (override)                             |
| ---------------------- | --------------------------------- | -------------------------------------------------- |
| **Source code**        | Baked into image at build time    | Bind-mounted from host (`.:/app`) — live reloading |
| **RNASEEK_ENV**        | `production`                      | `development`                                      |
| **SECRET_KEY**         | From `.env` (required)            | Hardcoded dev key (not for production)             |
| **Database**           | PostgreSQL                        | SQLite (`db.sqlite3`, auto-created)                |
| **Worker concurrency** | `$CELERY_CONCURRENCY` (default 4) | 2 (lighter on dev machines)                        |
| **Worker log level**   | `info`                            | `debug`                                            |
| **DEBUG**              | `False`                           | `True` (detailed error pages)                      |

### Bind Mount = Live Reloading

The dev override mounts your source code directory (`.:/app`) into every container. This means:
- Edit Python files on your host → changes are immediately visible inside the container.
- Daphne detects code changes and restarts automatically (Django's auto-reloader works through Daphne in debug mode).
- Celery worker requires a manual restart to pick up code changes (see [Section 3](#3-development-workflow)).

### File Structure Inside Container

```
/app/                          ← Your source code (bind-mounted)
/app/media/                    ← Upload/output directory (Docker volume)
/app/pipeline/reference_genomes/ ← Genome indexes (if present on host)
/app/db.sqlite3                ← SQLite database (bind-mounted, auto-created)
```

---

## 3. Development Workflow

### Starting / Stopping

```bash
# Start (foreground — logs in terminal)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Start (detached — background)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d

# Stop
docker compose down

# Stop and remove volumes (⚠️ deletes media uploads and DB)
docker compose down -v
```

### Viewing Logs

```bash
# All services
docker compose logs -f

# Just the web server
docker compose logs -f web

# Just the Celery worker (pipeline execution)
docker compose logs -f worker
```

### Restarting After Code Changes

**Web server:** Restarts automatically when Python files change (Django debug auto-reloader).

**Celery worker:** Must be restarted manually after changes to `pipeline/tasks/`:

```bash
docker compose restart worker
```

**Full rebuild** (after changing `environment.yml` or `requirements.txt`):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

### Running Django Management Commands

```bash
# Apply migrations
docker compose exec web python manage.py migrate

# Create a superuser (for /admin/)
docker compose exec web python manage.py createsuperuser

# Django shell
docker compose exec web python manage.py shell

# Collect static files
docker compose exec web python manage.py collectstatic --noinput

# System check
docker compose exec web python manage.py check
```

---

## 4. Running Tests

```bash
# All tests
docker compose exec web python manage.py test test -v 2

# Specific test modules
docker compose exec web python manage.py test test.test_entry_points -v 2
docker compose exec web python manage.py test test.test_assay_tracks -v 2
docker compose exec web python manage.py test test.test_stage2 -v 2

# Single test class
docker compose exec web python manage.py test test.test_assay_tracks.ChIPSeqRouteTest -v 2
```

---

## 5. Working Without Docker

If you prefer running Django directly on your host (e.g., for faster iteration or debugger support), you can skip Docker entirely for development:

```bash
# Activate the conda environment
conda activate rnaseek

# Start Redis (needed for Celery + WebSocket)
redis-server &

# Run Django dev server
python manage.py runserver

# In a separate terminal — start Celery worker
conda activate rnaseek
celery -A config worker --loglevel=debug --concurrency=2

# Or: run tasks synchronously (no worker needed)
CELERY_EAGER=1 python manage.py runserver
```

**When to use `CELERY_EAGER=1`:** Runs pipeline tasks inside the web process (synchronously). Useful for debugging task logic with breakpoints, but blocks the HTTP response until the task completes.

---

## 6. Switching to Production

When your code is ready for deployment:

1. **Push code** to your repository.

2. **On the production server**, pull and rebuild:
   ```bash
   git pull
   docker compose build
   docker compose up -d
   docker compose exec web python manage.py migrate
   ```

3. **Key differences** from dev:
   - Use only `docker-compose.yml` (no `-f docker-compose.dev.yml` override).
   - Set all environment variables in `.env` (see [Docker Deployment Guide](Docker%20Deployment%20Guide.md) § 3).
   - Add Nginx + SSL in front (see [Docker Deployment Guide](Docker%20Deployment%20Guide.md) § 7).
   - Bind-mount reference genomes from the host filesystem.

See [Docker Deployment Guide.md](Docker%20Deployment%20Guide.md) for the full production setup.

---

## 7. Common Tasks

### Access the Database

```bash
# SQLite (dev)
docker compose exec web python manage.py dbshell

# Or directly
sqlite3 db.sqlite3
```

### Inspect Celery

```bash
# Active tasks
docker compose exec worker celery -A config inspect active

# Registered tasks
docker compose exec worker celery -A config inspect registered

# Purge all pending tasks (⚠️ destructive)
docker compose exec worker celery -A config purge
```

### Clean Up Old Data

```bash
# Remove expired sessions and their files
docker compose exec web python manage.py shell -c "
from pipeline.tasks import purge_expired_sessions
purge_expired_sessions()
"
```

### Access a Shell Inside the Container

```bash
docker compose exec web bash
```

---

## 8. Troubleshooting

### Port 8000 already in use

Another process (maybe `python manage.py runserver`) is using port 8000. Either stop it or change the port:
```bash
RNASEEK_PORT=8001 docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### "ModuleNotFoundError" after adding a pip package

The container image is stale. Rebuild:
```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

### Celery worker not picking up tasks

1. Check Redis is running: `docker compose ps redis`
2. Check worker logs: `docker compose logs worker`
3. Restart the worker: `docker compose restart worker`

### Static files look broken / unstyled

Run collectstatic inside the container:
```bash
docker compose exec web python manage.py collectstatic --noinput
```

### Database migration errors

```bash
# Check migration status
docker compose exec web python manage.py showmigrations

# Apply pending migrations
docker compose exec web python manage.py migrate
```

### Pipeline fails with "HISAT2 index not found"

Reference genome files are not present. Either:
- Copy them to `pipeline/reference_genomes/` on your host (bind-mounted into container).
- Or use the Yeast genome (smallest at 44 MB) for development testing.

---

## Tip: Shell Alias

Add this to your `~/.bashrc` or `~/.zshrc` to avoid typing the long compose command:

```bash
alias dcu='docker compose -f docker-compose.yml -f docker-compose.dev.yml up'
alias dcd='docker compose down'
alias dcr='docker compose restart'
alias dce='docker compose exec web'
```

Then: `dcu --build` to start, `dce python manage.py migrate` to run commands.
