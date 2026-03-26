# RNAseek -- Docker Deployment Guide

> Production deployment using Docker Compose with 5 services: web, worker, beat, redis, and tusd.

---

## Prerequisites

- Docker Engine 24+
- Docker Compose V2
- A `.env` file with production secrets

---

## 1. Environment File

Create `.env` in the project root:

```
DJANGO_SECRET_KEY=<generate-a-64-char-random-string>
RNASEEK_ENV=production
DB_NAME=rnaseek
DB_USER=rnaseek
DB_PASSWORD=<your-postgres-password>
DB_HOST=host.docker.internal   # or your DB server IP
DB_PORT=5432
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0
MEDIA_ROOT=/app/media
DJANGO_ALLOWED_HOSTS=rnaseek.ca,www.rnaseek.ca
CSRF_TRUSTED_ORIGINS=https://rnaseek.ca,https://www.rnaseek.ca
RNASEEK_PORT=8000
CELERY_CONCURRENCY=4
```

---

## 2. Service Architecture

```
docker-compose.yml (5 services)

  +-------------+    +--------------+    +----------+    +---------+
  |    web       |    |   worker     |    |   beat   |    |  redis  |
  | (Daphne)    |    | (Celery)     |    | (Celery) |    | 7-alpine|
  | port 8000   |    | 32 GB limit  |    | 1 replica|    | port    |
  |             |    |              |    |          |    | 6379    |
  +------+------+    +------+-------+    +-----+----+    +----+----+
         |                  |                  |              |
         +------------------+------------------+--------------+
                            |
                     media-data volume
                     (/app/media/)
                            |
  +-------------------------+----+
  |          tusd                 |
  |   (tusproject/tusd:v2)       |
  |   port 127.0.0.1:1080       |
  |   uploads -> /app/media/     |
  +------------------------------+
```

| Service    | Image                  | Port            | Purpose                        |
| ---------- | ---------------------- | --------------- | ------------------------------ |
| `web`      | Custom (Dockerfile)    | 8000            | Daphne ASGI (HTTP + WebSocket) |
| `worker`   | Custom (Dockerfile)    | -               | Celery pipeline execution      |
| `beat`     | Custom (Dockerfile)    | -               | Celery Beat scheduler          |
| `redis`    | `redis:7-alpine`       | 6379 (internal) | Broker + Channels layer        |
| `tusd`     | `tusproject/tusd:v2`   | 1080 (loopback) | Tus resumable upload daemon    |

---

## 3. Build and Launch

```bash
# Build images
docker compose build

# Start all services (detached)
docker compose up -d

# Verify all 5 services are running
docker compose ps
```

Expected output: 5 services with status `Up (healthy)`.

---

## 4. Database Migrations

If using an external PostgreSQL (recommended for production):

```bash
docker compose exec web python manage.py migrate --noinput
```

---

## 5. Shared Volume

All services that handle pipeline data mount the `media-data` Docker volume at `/app/media/`. This ensures:

- The web server writes uploads visible to workers
- Workers write pipeline outputs visible to the web server
- tusd writes uploads to `/app/media/uploads/`, which Django can access
- Zero data copying between services

### NFS Volume (Multi-Host)

For multi-server deployments, replace the default Docker volume with an NFS mount in `docker-compose.yml`:

```yaml
volumes:
  media-data:
    driver: local
    driver_opts:
      type: nfs
      o: addr=<NFS_SERVER_IP>,rw,nfsvers=4.1,async,noatime,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2
      device: ":/exports/rnaseek"
```

---

## 6. tusd Configuration

The tusd service is configured in `docker-compose.yml` with these flags:

| Flag                              | Value                              | Purpose                               |
| --------------------------------- | ---------------------------------- | ------------------------------------- |
| `-upload-dir`                     | `/app/media/uploads`               | Upload storage path on shared volume  |
| `-base-path`                      | `/files/`                          | URL prefix (Nginx proxies here)       |
| `-hooks-http`                     | `http://web:8000/api/tusd-hooks/`  | Post-finish webhook to Django         |
| `-hooks-http-forward-headers`     | `Cookie,X-Session-ID`             | Session identification on hooks       |
| `-behind-proxy`                   | (flag)                             | Trust X-Forwarded-* from Nginx        |
| `-max-size`                       | `0`                                | Unlimited upload size                 |

tusd depends on the `web` service being healthy before starting, ensuring the webhook endpoint is available.

---

## 7. Health Checks

All services have built-in health checks:

```bash
# Check all service health
docker compose ps

# Individual service logs
docker compose logs -f web
docker compose logs -f worker
docker compose logs -f tusd
```

| Service  | Health Check                                               | Interval |
| -------- | ---------------------------------------------------------- | -------- |
| `web`    | Python urllib HTTP request to `localhost:8000/`             | 30s      |
| `worker` | `celery -A config inspect ping`                            | 60s      |
| `redis`  | `redis-cli ping`                                           | 10s      |
| `tusd`   | `wget --spider -q http://localhost:1080/files/`            | 30s      |

---

## 8. Scaling Workers

Increase Celery concurrency via the environment variable:

```bash
CELERY_CONCURRENCY=8 docker compose up -d worker
```

Or add additional worker replicas:

```bash
docker compose up -d --scale worker=2
```

---

## 9. Stopping and Restarting

```bash
# Stop all services
docker compose down

# Stop and remove volumes (destructive -- deletes all media data)
docker compose down -v

# Restart a single service
docker compose restart web

# Rebuild and restart (after code changes)
docker compose build && docker compose up -d
```

---

## 10. Nginx Integration

When running Docker on the same host as Nginx, tusd listens on `127.0.0.1:1080` and Daphne on port 8000. Nginx proxies:

- `/files/` to `127.0.0.1:1080` (tusd) with `proxy_request_buffering off`
- `/ws/` to `127.0.0.1:8000` (Daphne) with WebSocket upgrade
- `/*` to `127.0.0.1:8000` (Daphne)

See `nginx/rnaseek.conf` for the complete configuration.
