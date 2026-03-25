# RNAseek — Docker Deployment Guide

> **Applies to:** Production deployments using Docker Compose  
> **Alternative:** See [Production Deployment Guide.md](Production%20Deployment%20Guide.md) for bare-metal (non-Docker) deployment.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Environment Configuration](#3-environment-configuration)
4. [Reference Genomes](#4-reference-genomes)
5. [Build & Launch](#5-build--launch)
6. [Database Setup](#6-database-setup)
7. [SSL / Reverse Proxy](#7-ssl--reverse-proxy)
8. [Scaling Workers](#8-scaling-workers)
9. [Persistent Storage](#9-persistent-storage)
10. [Monitoring & Logs](#10-monitoring--logs)
11. [Updates & Redeployment](#11-updates--redeployment)
12. [Verification Checklist](#12-verification-checklist)
13. [Troubleshooting](#13-troubleshooting)

---

## 1. Architecture Overview

Docker Compose runs four services from a single image:

```
┌──────────────────────────────────────────────────────┐
│  Host / VM                                           │
│                                                      │
│  ┌──────────────┐  ┌────────────────┐  ┌──────────┐ │
│  │   web        │  │   worker       │  │   beat   │ │
│  │ (Daphne ASGI)│  │ (Celery worker)│  │ (Celery  │ │
│  │  HTTP + WS   │  │  Pipeline exec │  │  sched.) │ │
│  └──────┬───────┘  └───────┬────────┘  └────┬─────┘ │
│         │                  │                 │       │
│         ▼                  ▼                 ▼       │
│  ┌──────────────────────────────────────────────────┐│
│  │            redis (broker + channels)              ││
│  └──────────────────────────────────────────────────┘│
│                                                      │
│  ┌─────────────────┐  ┌────────────────────────────┐ │
│  │  media-data vol  │  │  ref-genomes bind mount    │ │
│  │ (uploads/output) │  │ (44 GB HISAT2 indexes)     │ │
│  └─────────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

| Service    | Image            | Role                                                                                |
| ---------- | ---------------- | ----------------------------------------------------------------------------------- |
| **web**    | `rnaseek:latest` | Daphne ASGI — serves HTTP + WebSocket (real-time progress)                          |
| **worker** | `rnaseek:latest` | Celery workers — runs bioinformatics pipeline (HISAT2, featureCounts, DESeq2, etc.) |
| **beat**   | `rnaseek:latest` | Celery Beat — schedules `purge_expired_sessions` at 2:00 AM daily                   |
| **redis**  | `redis:7-alpine` | Message broker (Celery) + channel layer (Django Channels)                           |

All four services share a `media-data` volume so uploads and pipeline outputs are visible to both `web` (for downloads) and `worker` (for processing).

---

## 2. Prerequisites

| Requirement        | Minimum         | Recommended                                              |
| ------------------ | --------------- | -------------------------------------------------------- |
| **Docker Engine**  | 24.0+           | 27.0+                                                    |
| **Docker Compose** | v2.20+ (plugin) | v2.30+                                                   |
| **RAM**            | 16 GB           | 32+ GB (HISAT2 on human genome uses ~8 GB per alignment) |
| **Disk**           | 100 GB          | 500 GB+ (reference genomes ≈ 44 GB, user data grows)     |
| **CPU**            | 4 cores         | 16+ cores (parallel alignment + DESeq2)                  |

Install Docker Engine on Ubuntu:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# Log out and back in, then verify:
docker --version
docker compose version
```

---

## 3. Environment Configuration

Create a `.env` file in the project root. The `docker-compose.yml` reads it via `env_file: .env`.

```bash
cp .env.prod .env
nano .env
```

Required variables:

```ini
# ── Core Django ──
RNASEEK_ENV=production
DJANGO_SECRET_KEY=<generate below>
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com

# ── Database (PostgreSQL — external or containerized) ──
DB_NAME=rnaseek
DB_USER=rnaseek
DB_PASSWORD=<strong-random-password>
DB_HOST=host.docker.internal    # If PG runs on the host
DB_PORT=5432

# ── Redis (auto-configured by Compose, but override if external) ──
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# ── File Storage ──
MEDIA_ROOT=/app/media

# ── Worker Tuning ──
CELERY_CONCURRENCY=2            # Jobs in parallel (see Section 8)

# ── Port ──
RNASEEK_PORT=8000               # Host port for web service
```

Generate a secret key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

### PostgreSQL Options

**Option A — External PostgreSQL (recommended for production):**
Run PostgreSQL on the host or a managed service (AWS RDS, etc.). Set `DB_HOST` to the host address.

**Option B — Containerized PostgreSQL:**
Add a `postgres` service to `docker-compose.yml`:

```yaml
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: rnaseek
      POSTGRES_USER: rnaseek
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pg-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rnaseek"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
```

Then update `x-app-common` to set `DB_HOST=postgres` and add `depends_on: postgres`.

---

## 4. Reference Genomes

Reference genomes (~44 GB) are **too large for the Docker image** and **too large for Git**. They are bind-mounted from the host filesystem into the container.

### 4.1 Transfer Genomes to the Host

```bash
# Create the directory on the production host
sudo mkdir -p /data/rnaseek/reference_genomes

# Copy from your development machine
rsync -avz --progress \
    /path/to/dev/pipeline/reference_genomes/ \
    user@production-host:/data/rnaseek/reference_genomes/
```

### 4.2 Bind Mount in docker-compose.yml

Add the bind mount to the `x-app-common` volumes:

```yaml
x-app-common: &app-common
  build: .
  env_file: .env
  volumes:
    - media-data:/app/media
    - /data/rnaseek/reference_genomes:/app/pipeline/reference_genomes:ro
```

The `:ro` (read-only) flag prevents the container from modifying the genome files. Remove `:ro` if custom genome HISAT2 index building needs to write back.

### 4.3 Verify Inside Container

```bash
docker compose exec web ls /app/pipeline/reference_genomes/
# Should list: Arabidopsis_TAIR10/ Celegans_WBcel235/ Chicken_GRCg6a/ ...
```

> **See also:** [Reference Genome Strategy.md](Reference%20Genome%20Strategy.md) for the full strategy on building, distributing, and managing genome indexes.

---

## 5. Build & Launch

```bash
cd /path/to/rnaseek

# Build the Docker image (first time takes ~15–20 minutes for conda)
docker compose build

# Start all services in detached mode
docker compose up -d

# View logs (follow mode)
docker compose logs -f
```

The Dockerfile builds in three cached layers:
1. **Conda environment** (R 4.3, HISAT2, SAMtools, etc.) — cached unless `environment.yml` changes
2. **Pip packages** (Django, Celery, rpy2, etc.) — cached unless `requirements.txt` changes
3. **Application code** — rebuilds on any code change

Subsequent builds after code changes are fast (~30 seconds) because layers 1 and 2 are cached.

---

## 6. Database Setup

After the first launch, apply Django migrations:

```bash
# Run migrations
docker compose exec web python manage.py migrate

# Verify Django configuration
docker compose exec web python manage.py check --deploy
```

---

## 7. SSL / Reverse Proxy

The `web` container exposes Daphne on `${RNASEEK_PORT:-8000}`. In production, place an **Nginx reverse proxy** on the host to handle SSL termination.

### Option A — Nginx on the Host

Install Nginx and Certbot directly on the host:

```bash
sudo apt install nginx certbot python3-certbot-nginx
```

Use the Nginx config from [Production Deployment Guide.md § 13](Production%20Deployment%20Guide.md), replacing `127.0.0.1:8099` with `127.0.0.1:8000` (or your `RNASEEK_PORT` value).

Key config points:
- `proxy_pass http://127.0.0.1:8000;` for HTTP requests
- `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade;` for WebSocket at `/ws/`
- `client_max_body_size 50G;` for large FASTQ uploads

Then obtain an SSL certificate:

```bash
sudo certbot --nginx -d your-domain.com
```

### Option B — Containerized Reverse Proxy

Use [traefik](https://doc.traefik.io/traefik/) or [caddy](https://caddyserver.com/) as a container with automatic Let's Encrypt certificate management. This is more complex but keeps everything in Docker.

---

## 8. Scaling Workers

Each Celery worker task runs a full bioinformatics pipeline that is CPU- and memory-intensive. Tuning concurrency prevents resource starvation.

### Concurrency Formula

```
CELERY_CONCURRENCY = floor(total_CPU_cores / threads_per_tool)
```

HISAT2 uses `CPU_COUNT // 2` threads per sample. On a 16-core machine:
- `threads_per_tool = 8`
- `CELERY_CONCURRENCY = 2` → 2 simultaneous pipeline jobs

### Horizontal Scaling

To add more worker instances:

```bash
docker compose up -d --scale worker=3
```

This launches 3 worker containers, each with `${CELERY_CONCURRENCY}` concurrent tasks. Ensure sufficient RAM — each worker needs up to 16 GB for human genome alignment.

### Memory Limits

The `worker` service has a 32 GB memory limit (`deploy.resources.limits.memory: 32G`). Adjust in `docker-compose.yml` based on your available resources.

---

## 9. Persistent Storage

| Volume                 | Type           | Purpose                                             | Backup?                          |
| ---------------------- | -------------- | --------------------------------------------------- | -------------------------------- |
| `media-data`           | Docker volume  | User uploads, pipeline outputs, session directories | **Yes** — contains all user data |
| `redis-data`           | Docker volume  | Redis AOF/RDB persistence                           | Optional — ephemeral job state   |
| Ref genomes bind mount | Host directory | HISAT2/BWA/Bismark indexes                          | No — reproducible from source    |

### Backups

```bash
# Backup media volume to a tarball
docker run --rm -v rnaseek_media-data:/data -v $(pwd):/backup \
    alpine tar czf /backup/media-backup-$(date +%Y%m%d).tar.gz -C /data .

# Restore
docker run --rm -v rnaseek_media-data:/data -v $(pwd):/backup \
    alpine tar xzf /backup/media-backup-20250101.tar.gz -C /data
```

### NFS Mount (Multi-Server)

For multi-server deployments where web and worker containers run on different hosts, replace the Docker volume with an NFS mount:

```yaml
volumes:
  media-data:
    driver: local
    driver_opts:
      type: nfs
      o: addr=<NFS_SERVER_IP>,rw,nfsvers=4.1
      device: ":/exports/rnaseek"
```

---

## 10. Monitoring & Logs

### View Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f worker

# Last 100 lines
docker compose logs --tail=100 web
```

### Health Checks

All services have health checks defined in `docker-compose.yml`:

```bash
# Check service health
docker compose ps
# Healthy services show "(healthy)" in the STATUS column
```

### Celery Inspection

```bash
# List active tasks
docker compose exec worker celery -A config inspect active

# Registered tasks
docker compose exec worker celery -A config inspect registered

# Worker stats
docker compose exec worker celery -A config inspect stats
```

---

## 11. Updates & Redeployment

```bash
cd /path/to/rnaseek

# Pull latest code
git pull

# Rebuild image (only changed layers rebuild)
docker compose build

# Apply database migrations
docker compose exec web python manage.py migrate

# Rolling restart (zero downtime for web, workers drain current tasks)
docker compose up -d
```

For zero-downtime deployments, consider running two worker instances and restarting them one at a time.

---

## 12. Verification Checklist

```bash
# 1. All services running and healthy
docker compose ps

# 2. Django system check
docker compose exec web python manage.py check --deploy

# 3. Database connected
docker compose exec web python manage.py showmigrations | head -5

# 4. Redis connected
docker compose exec redis redis-cli ping
# → PONG

# 5. Celery worker registered
docker compose exec worker celery -A config inspect registered | grep run_core_pipeline

# 6. Static files served
curl -I http://localhost:${RNASEEK_PORT:-8000}/static/pipeline/css/global.css

# 7. Reference genomes mounted
docker compose exec web ls /app/pipeline/reference_genomes/ | wc -l
# → 11

# 8. WebSocket endpoint accessible
curl -i -N -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGVzdA==" \
    http://localhost:${RNASEEK_PORT:-8000}/ws/pipeline/00000000-0000-0000-0000-000000000000/
```

---

## 13. Troubleshooting

### Container won't start — `conda env update` fails

The Miniconda base image occasionally changes package resolution. Pin the exact image tag in `Dockerfile`:
```dockerfile
FROM continuumio/miniconda3:25.1.1-2 AS base
```

### "502 Bad Gateway" from Nginx

Daphne hasn't started yet. Check:
```bash
docker compose logs web | tail -20
```

Common causes: missing `.env` file, database unreachable, port conflict.

### Celery tasks stuck in PENDING

```bash
# Check Redis is reachable from worker
docker compose exec worker python -c "import redis; r=redis.from_url('redis://redis:6379/0'); print(r.ping())"

# Check worker is running and connected
docker compose exec worker celery -A config inspect ping
```

### "HISAT2 index not found"

Reference genomes aren't mounted. Verify the bind mount:
```bash
docker compose exec worker ls /app/pipeline/reference_genomes/Human_GRCh38/
```

### Out of memory during alignment

HISAT2 on human genome requires ~8 GB RAM per concurrent alignment. Reduce `CELERY_CONCURRENCY` or increase the `deploy.resources.limits.memory` in `docker-compose.yml`.

### Permission denied on media directory

The container runs as root by default. If using a non-root user, ensure the media volume is writable:
```bash
docker compose exec web chown -R $(id -u):$(id -g) /app/media
```

---

## Quick-Start Command Summary

```bash
# 1. Configure
cp .env.prod .env && nano .env

# 2. Place reference genomes on host
sudo mkdir -p /data/rnaseek/reference_genomes
rsync -avz dev:/path/to/reference_genomes/ /data/rnaseek/reference_genomes/

# 3. Add bind mount to docker-compose.yml (Section 4.2)

# 4. Build and launch
docker compose build && docker compose up -d

# 5. Database setup
docker compose exec web python manage.py migrate
docker compose exec web python manage.py check --deploy

# 6. Set up Nginx + SSL on host (Section 7)

# 7. Verify
docker compose ps
docker compose exec web python manage.py check --deploy
```
