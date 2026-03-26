# RNAseek -- Docker Development Guide

> Local development using Docker Compose with live code reloading and debug settings.

---

## Quick Start

```bash
# Build images
docker compose build

# Start with dev overrides (live reload, debug logging, reduced concurrency)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

---

## Dev Override File

`docker-compose.dev.yml` applies these changes on top of the production stack:

| Setting              | Production                    | Development                       |
| -------------------- | ----------------------------- | --------------------------------- |
| Source code           | Baked into Docker image       | Live-mounted from host (`.:/app`) |
| Environment           | `RNASEEK_ENV=production`      | `RNASEEK_ENV=development`         |
| Secret key            | From `.env`                   | Hardcoded dev key                 |
| Worker concurrency   | `${CELERY_CONCURRENCY:-4}`    | `2`                               |
| Worker log level     | `info`                        | `debug`                           |
| Database              | PostgreSQL                    | SQLite (`db.sqlite3`)             |

---

## How It Works

The dev override mounts the project directory as a volume (`.:/app`), so code changes are immediately visible inside the containers without rebuilding. The media volume is preserved separately.

---

## Common Workflows

### Rebuild After Dependency Changes

If `requirements.txt` or `environment.yml` changes:

```bash
docker compose build
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### Run Migrations

```bash
docker compose exec web python manage.py migrate
```

### Open Django Shell

```bash
docker compose exec web python manage.py shell
```

### Run Tests

```bash
docker compose exec web python manage.py test test -v2
```

### View Logs

```bash
# All services
docker compose logs -f

# Single service
docker compose logs -f worker
```

### Stop and Clean Up

```bash
# Stop services (preserve data)
docker compose down

# Stop and delete all volumes (fresh start)
docker compose down -v
```

---

## Service Access Points

| Service   | URL                           | Notes                     |
| --------- | ----------------------------- | ------------------------- |
| Web       | `http://localhost:8000`       | Django application        |
| tusd      | `http://localhost:1080/files/`| Tus upload endpoint       |
| Redis     | `redis://localhost:6379`      | Broker (internal only)    |
| WebSocket | `ws://localhost:8000/ws/`     | Real-time progress        |

---

## Running Without Docker

For direct local development without Docker, see the Development Guide in `doc/guide/Development/`.
