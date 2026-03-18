# RNAseek — Production Deployment Guide

> **Target OS:** Ubuntu 22.04+ LTS (bare-metal or VM)
> **Last updated:** March 18, 2026
> **Live deployment:** rnaseek.ca — bare-metal, `ubuntu` user, conda env at `/opt/miniconda3/envs/rnaseek` (Python 3.11 + R 4.3)

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [System Packages](#2-system-packages)
3. [Conda Environment Setup](#3-conda-environment-setup)
4. [Project Files](#4-project-files)
5. [PostgreSQL Database](#5-postgresql-database)
6. [Redis](#6-redis)
7. [Environment Variables](#7-environment-variables)
8. [Django Setup](#8-django-setup)
9. [Static Files](#9-static-files)
10. [Reference Genomes](#10-reference-genomes)
11. [Daphne (ASGI Server)](#11-daphne-asgi-server)
12. [Celery Worker](#12-celery-worker)
13. [Nginx Reverse Proxy](#13-nginx-reverse-proxy)
14. [SSL / HTTPS](#14-ssl--https)
15. [Systemd Services](#15-systemd-services)
16. [Session Cleanup Cron](#16-session-cleanup-cron)
17. [Firewall](#17-firewall)
18. [Verification Checklist](#18-verification-checklist)
19. [Troubleshooting](#19-troubleshooting)

---

## 1. Prerequisites

| Requirement | Minimum             | Recommended                                       |
| ----------- | ------------------- | ------------------------------------------------- |
| **CPU**     | 4 cores             | 16+ cores (pipeline parallelism)                  |
| **RAM**     | 8 GB                | 32+ GB (DESeq2 / large genomes)                   |
| **Disk**    | 100 GB SSD          | 500 GB+ NVMe (reference genomes are ~44 GB alone) |
| **OS**      | Ubuntu 22.04 LTS    | Ubuntu 24.04 LTS                                  |
| **Network** | Public IP or domain | Domain with DNS configured                        |

The pipeline is CPU- and memory-intensive. HISAT2 alignment, featureCounts, DESeq2, and HISAT2 index building all scale with thread count. The more cores available, the faster jobs complete.

---

## 2. System Packages

```bash
# Update and install system-level dependencies
sudo apt update && sudo apt upgrade -y
sudo apt install -y \
    build-essential \
    git \
    curl \
    wget \
    nginx \
    postgresql \
    postgresql-contrib \
    redis-server \
    supervisor \
    pkg-config \
    libpq-dev \
    libhdf5-dev \
    libxml2-dev \
    libcurl4-openssl-dev \
    libssl-dev \
    certbot \
    python3-certbot-nginx
```

**What each group does:**
- `build-essential`, `pkg-config`, `libpq-dev`, `libhdf5-dev` — Compilation toolchain needed by Python packages like `psycopg2`, `h5py`, `rpy2`.
- `nginx` — Reverse proxy that sits in front of Daphne, terminates SSL, serves static files, and handles WebSocket upgrade requests.
- `postgresql`, `postgresql-contrib` — Production database. SQLite is only used in development; PostgreSQL handles concurrent writes from Celery workers safely.
- `redis-server` — Message broker for Celery task queue and Django Channels WebSocket layer.
- `certbot`, `python3-certbot-nginx` — Free SSL certificates from Let's Encrypt.

---

## 3. Python Environment Setup

The rnaseek.ca production deployment uses **system Python 3.10** with pip-installed packages (no conda). R and bioinformatics CLI tools are optional — they are only needed if running the FASTQ/alignment pipeline tracks on the server. If you only serve the web UI and run Stage 2 stats (or run pipelines via Docker workers), the core web stack needs only the pip packages.

### Option A: System Python + pip (current rnaseek.ca setup)

```bash
# Install pip dependencies into ~/.local/
cd /home/ubuntu/apps/rnaseek
pip3 install -r requirements.txt
```

Note: rpy2 and R-based features (DESeq2, WGCNA) will be unavailable. The `_r_bridge.py` module handles this gracefully — R imports are conditional and fall back to `None`.

### Option B: Conda (full pipeline support)

If you need R, Bioconductor, and CLI bioinformatics tools:

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p /opt/miniconda3
eval "$(/opt/miniconda3/bin/conda shell.bash hook)"
conda init bash && source ~/.bashrc

cd /home/ubuntu/apps/rnaseek
conda env create -f environment.yml
conda activate rnaseek
pip install -r requirements.txt
```

Verify critical tools:

```bash
python3 -c "import django; print(django.get_version())"
# If conda: which hisat2 && which samtools && which featureCounts
```

---

## 4. Project Files

```bash
# Clone the repository
mkdir -p /home/ubuntu/apps
git clone <your-repo-url> /home/ubuntu/apps/rnaseek
cd /home/ubuntu/apps/rnaseek
```

**Directory layout in production (rnaseek.ca):**

```
/home/ubuntu/apps/rnaseek/              ← Application code (git repo)
/home/ubuntu/apps/rnaseek/staticfiles/  ← Collected static files (collectstatic)
/home/ubuntu/apps/rnaseek/media/        ← User uploads & pipeline outputs (MEDIA_ROOT)
/home/ubuntu/apps/rnaseek/reference_genomes/ ← Pre-built HISAT2 indexes
```

Create the media directory:

```bash
mkdir -p /home/ubuntu/apps/rnaseek/media/sessions
```

> **Note:** On rnaseek.ca, `MEDIA_ROOT` points to `/home/ubuntu/apps/rnaseek/media`. For servers with limited root disk space, consider using a separate `/data/` mount and symlinking.

---

## 5. PostgreSQL Database

SQLite cannot handle concurrent writes from multiple Celery workers — PostgreSQL is required in production.

```bash
# Start and enable PostgreSQL
sudo systemctl enable postgresql
sudo systemctl start postgresql

# Create database and user
sudo -u postgres psql <<SQL
CREATE USER rnaseek WITH PASSWORD '<strong-password>';
CREATE DATABASE rnaseek OWNER rnaseek;
GRANT ALL PRIVILEGES ON DATABASE rnaseek TO rnaseek;
\q
SQL
```

**Security notes:**
- Use a strong, randomly generated password (e.g., `openssl rand -base64 32`).
- PostgreSQL listens on `127.0.0.1:5432` by default — this is correct for a single-server deployment. If the DB is on a separate host, configure `pg_hba.conf` accordingly.

---

## 6. Redis

Redis serves two roles in RNAseek:
1. **Celery broker** — Distributes pipeline tasks to worker processes.
2. **Django Channels layer** — Routes WebSocket messages for real-time progress updates.

```bash
sudo systemctl enable redis-server
sudo systemctl start redis-server

# Verify
redis-cli ping
# → PONG
```

Default config (`127.0.0.1:6379`) works for single-server setups. For multi-server, bind to the private network interface and set a password in `/etc/redis/redis.conf`.

---

## 7. Environment Variables

RNAseek reads all configuration from environment variables. Create a `.env` file in the project root:

```bash
cd /home/ubuntu/apps/rnaseek
nano .env
```

The `.env` file should contain:

```ini
# ── Core ──
RNASEEK_ENV=production
DJANGO_SECRET_KEY=<generate-with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DJANGO_ALLOWED_HOSTS=your-domain.com,www.your-domain.com

# ── PostgreSQL ──
DB_NAME=rnaseek
DB_USER=rnaseek
DB_PASSWORD=<the-password-from-step-5>
DB_HOST=127.0.0.1
DB_PORT=5432

# ── Redis ──
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
REDIS_URL=redis://127.0.0.1:6379/0

# ── File Storage ──
MEDIA_ROOT=/home/ubuntu/apps/rnaseek/media
```

**How the settings module uses these:**
- `RNASEEK_ENV=production` flips `IS_PRODUCTION=True`, enabling PostgreSQL, `DEBUG=False`, HSTS headers, SSL redirect, and secure cookies.
- `DJANGO_SECRET_KEY` — Cryptographically signs sessions/cookies. Must be unique and secret per deployment.
- `DJANGO_ALLOWED_HOSTS` — Django rejects HTTP requests whose `Host` header doesn't match this list. Set to your actual domain(s).

Generate a secret key:

```bash
python3 -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

---

## 8. Django Setup

```bash
cd /home/ubuntu/apps/rnaseek

# Apply database migrations (creates all tables in PostgreSQL)
python3 manage.py migrate

# Verify no configuration issues
python3 manage.py check --deploy
```

The `--deploy` flag checks for common production misconfigurations (insecure settings, missing HSTS, etc.). Fix any warnings it reports.

---

## 9. Static Files

RNAseek uses **WhiteNoise** to serve static files (CSS, JS, images) directly from the ASGI server without needing Nginx to handle them. This simplifies the deployment and ensures cache-busting via hashed filenames.

```bash
python3 manage.py collectstatic --noinput
```

This copies all static files from `pipeline/static/` into `staticfiles/` and generates compressed, content-hashed versions (e.g., `global.a1b2c3d4.css`). WhiteNoise serves them with far-future `Cache-Control` headers for optimal performance.

---

## 10. Reference Genomes

The pre-built HISAT2 indexes (~44 GB total for all 11 organisms) are **NOT stored in Git** — they must be transferred separately to the production server.

```bash
# Option A: rsync from dev machine
rsync -avz --progress \
    /path/to/dev/rnaseek/reference_genomes/ \
    user@production-server:/home/ubuntu/apps/rnaseek/reference_genomes/

# Option B: Download and rebuild (takes hours for large genomes)
# See the reference genome scripts in the project docs.
```

**What's in reference_genomes/:**
Each organism subdirectory (at the project root, NOT under `pipeline/`) contains:
- `.fa` — Genome FASTA sequence (used by samtools for CRAM→BAM conversion)
- `.gtf` — Gene annotation (used by featureCounts for quantification)
- `.1.ht2` through `.8.ht2` — Pre-built HISAT2 index files (used for read alignment)

| Organism              | Size   |
| --------------------- | ------ |
| Human (GRCh38)        | 8.5 GB |
| Mouse (GRCm38)        | 7.4 GB |
| Mouse (GRCm39)        | 7.2 GB |
| Rat (rn7)             | 6.3 GB |
| Pig (Sscrofa11.1)     | 6.3 GB |
| Zebrafish (GRCz11)    | 3.7 GB |
| Chicken (GRCg6a)      | 2.8 GB |
| Arabidopsis (TAIR10)  | 555 MB |
| Drosophila (dm6)      | 525 MB |
| C. elegans (WBcel235) | 422 MB |
| Yeast (sacCer3)       | 44 MB  |

Verify the indexes are intact:

```bash
ls /home/ubuntu/apps/rnaseek/reference_genomes/Human_GRCh38/*.ht2 | wc -l
# → 8  (one index has 8 segment files)
```

---

## 11. Daphne (ASGI Server)

RNAseek uses **Daphne** (the Django Channels ASGI server) instead of Gunicorn because the application requires WebSocket support for real-time pipeline progress updates. Gunicorn only handles HTTP; Daphne handles both HTTP and WebSocket connections.

**How it fits in the stack:**
```
Client → Nginx (port 80/443) → Daphne (port 8099) → Django/Channels
```

Test that Daphne starts correctly:

```bash
cd /home/ubuntu/apps/rnaseek
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

You should see:

```
Starting server at tcp:port=8000:interface=127.0.0.1
HTTP/2 support not enabled (install the http2 and tls Twisted extras)
Configuring endpoint tcp:port=8000:interface=127.0.0.1
Listening on TCP address 127.0.0.1:8000
```

> **Bind to 127.0.0.1**, not `0.0.0.0`. Nginx will proxy to Daphne on localhost — there's no reason for Daphne to be directly accessible from the internet.

---

## 12. Celery Worker

Celery processes the bioinformatics pipeline tasks in the background. Each submitted analysis job is picked up by a Celery worker, which runs the full pipeline (FastQC → Trimmomatic → HISAT2 → featureCounts → DESeq2).

```bash
cd /home/ubuntu/apps/rnaseek

# Test start (foreground)
celery -A config worker \
    --loglevel=info \
    --concurrency=4 \
    --pool=prefork \
    --max-tasks-per-child=50
```

**Concurrency considerations:**
- Each pipeline task is CPU-intensive (HISAT2 uses multiple threads internally).
- `--concurrency=4` means at most 4 pipeline jobs run simultaneously.
- Inside each job, tools like HISAT2 and featureCounts use multiple threads (auto-detected from `os.cpu_count()`).
- `--max-tasks-per-child=50` recycles worker processes after 50 tasks to prevent memory leaks.
- **Rule of thumb:** Set `--concurrency` to `floor(total_cores / threads_per_tool)`. On a 16-core machine with HISAT2 using 8 threads per sample, use `--concurrency=2`.

> **Do NOT set high concurrency** (e.g., 16) — each pipeline task already internally parallelizes across multiple threads. High Celery concurrency would oversubscribe the CPU and slow everything down.

---

## 13. Nginx Reverse Proxy

Nginx sits in front of Daphne, handling:
- SSL termination (HTTPS)
- Static file serving (optional, WhiteNoise handles this too)
- WebSocket upgrade (`Upgrade: websocket` headers)
- Request buffering and rate limiting

The actual nginx config is maintained in the repo at `nginx/rnaseek.conf` and symlinked into Nginx:

```bash
sudo ln -sf /home/ubuntu/apps/rnaseek/nginx/rnaseek.conf /etc/nginx/sites-enabled/rnaseek.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Key settings in the config:
- `client_max_body_size 10G` allows large FASTQ uploads (chunked at 5 MB per request, but the limit covers edge cases)
- Static files served directly by Nginx via `location /static/` for performance
- WebSocket location `/ws/` with `proxy_read_timeout 86400` for long-lived connections
- All other requests proxied to Daphne on `127.0.0.1:8000`

See [nginx/rnaseek.conf](../../nginx/rnaseek.conf) for the full configuration.

Enable the site:

```bash
sudo ln -s /etc/nginx/sites-available/rnaseek /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # Remove default site
sudo nginx -t                                   # Test config
sudo systemctl reload nginx
```

---

## 14. SSL / HTTPS

Use Let's Encrypt (free) via Certbot:

```bash
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Certbot will:
1. Verify domain ownership via an HTTP challenge.
2. Generate and install SSL certificates.
3. Auto-modify the Nginx config with certificate paths.
4. Set up auto-renewal via a systemd timer.

Verify auto-renewal:

```bash
sudo certbot renew --dry-run
```

---

## 15. Systemd Services

The systemd unit files are maintained in the repo under `systemd/` and copied to `/etc/systemd/system/` during deployment.

Three services run on rnaseek.ca:

| Service | Unit file | Purpose |
|---|---|---|
| `rnaseek-web` | `rnaseek-web.service` | Daphne ASGI server on 127.0.0.1:8000 |
| `rnaseek-worker` | `rnaseek-worker.service` | Celery worker (concurrency=4, max-tasks-per-child=50) |
| `rnaseek-beat` | `rnaseek-beat.service` | Celery Beat scheduler (purge_expired_sessions at 2 AM UTC) |

Install and enable:

```bash
sudo cp /home/ubuntu/apps/rnaseek/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rnaseek-web rnaseek-worker rnaseek-beat

# Check status
sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat
```

See [systemd/](../../systemd/) for the actual unit files.

**Important notes from deployment experience:**
- `ProtectSystem=strict` causes `NAMESPACE` errors on some Ubuntu setups. The production unit files intentionally omit this directive.
- Systemd does not support bash variable substitution in `ExecStart`. Use literal paths (e.g., `/home/ubuntu/.local/bin/daphne`), not `$HOME/.local/bin/daphne`.
- The `EnvironmentFile` directive loads the `.env` file so Django picks up all production settings.

---

## 16. Session Cleanup (Celery Beat)

RNAseek sessions expire after 14 days. Expired sessions and their associated files are purged automatically by the `purge_expired_sessions` Celery task, scheduled via Celery Beat at 2:00 AM UTC daily.

The `rnaseek-beat` systemd service handles this. No separate cron job is needed.

---

## 17. Firewall

Only expose the ports that Nginx needs:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'   # ports 80 + 443
sudo ufw enable
sudo ufw status
```

**Do NOT expose:**
- Port 8000 (Daphne) — Only Nginx should reach it via localhost.
- Port 5432 (PostgreSQL) — Only local connections.
- Port 6379 (Redis) — Only local connections.

---

## 18. Verification Checklist

Run through these checks after deployment:

```bash
# 1. Django system check (should report 0 issues)
cd /home/ubuntu/apps/rnaseek
python3 manage.py check --deploy

# 2. Database connectivity
python3 manage.py showmigrations | head -5

# 3. Redis connectivity
redis-cli ping

# 4. Static files collected
ls staticfiles/pipeline/css/global.*.css

# 5. Daphne is running
sudo systemctl status rnaseek-web

# 6. Celery is running and connected
sudo systemctl status rnaseek-worker

# 7. Celery Beat is running
sudo systemctl status rnaseek-beat

# 8. Nginx is running
sudo systemctl status nginx

# 9. SSL certificate is valid
curl -I https://rnaseek.ca

# 10. Reference genomes are present
ls /home/ubuntu/apps/rnaseek/reference_genomes/ | wc -l

# 11. Upload a test file via the browser and verify it appears in MEDIA_ROOT
ls /home/ubuntu/apps/rnaseek/media/sessions/
```

---

## 19. Troubleshooting

### "502 Bad Gateway" from Nginx
Daphne isn't running or crashed. Check:
```bash
sudo systemctl status rnaseek-web
sudo journalctl -u rnaseek-web -n 50
```

### Static files return 404
Run `python3 manage.py collectstatic --noinput` and restart Daphne. WhiteNoise caches the manifest at startup.
```bash
python3 manage.py collectstatic --noinput
sudo systemctl restart rnaseek-web
```

### WebSocket connection fails
1. Verify the Nginx `location /ws/` block includes `proxy_http_version 1.1` and `Upgrade` headers.
2. Check Daphne logs: `sudo journalctl -u rnaseek-web | grep -i websocket`

### Celery tasks stuck in PENDING
1. Verify Redis is running: `redis-cli ping`
2. Check Celery is connected: `sudo journalctl -u rnaseek-worker | grep "Connected to"`
3. Verify task is registered: `celery -A config inspect registered | grep run_core_pipeline`

### "HISAT2 index not found" errors
Reference genomes weren't transferred. Check:
```bash
ls /home/ubuntu/apps/rnaseek/reference_genomes/Human_GRCh38/*.ht2
```

### Permission denied on media directory
```bash
sudo chown -R ubuntu:ubuntu /home/ubuntu/apps/rnaseek/media
chmod -R 755 /home/ubuntu/apps/rnaseek/media
```

### DESeq2 / R errors
On rnaseek.ca, R/rpy2 is not installed (system Python only). The `_r_bridge.py` module logs a warning and R-based pipeline steps will be unavailable. If R is needed, use the conda environment setup (Section 3, Option B).

---

## Quick-Start Command Summary

```bash
# On a fresh Ubuntu 22.04 server, run in order:

# 1. System packages
sudo apt update && sudo apt install -y build-essential git curl wget nginx \
    postgresql postgresql-contrib redis-server pkg-config libpq-dev \
    libhdf5-dev libxml2-dev libcurl4-openssl-dev libssl-dev \
    certbot python3-certbot-nginx

# 2. Project
git clone <repo-url> /home/ubuntu/apps/rnaseek && cd /home/ubuntu/apps/rnaseek
pip3 install -r requirements.txt

# 3. Config
nano .env   # Set RNASEEK_ENV, DJANGO_SECRET_KEY, DB creds, ALLOWED_HOSTS

# 4. Database
sudo -u postgres psql -c "CREATE USER rnaseek WITH PASSWORD 'xxx'; CREATE DATABASE rnaseek OWNER rnaseek;"
python3 manage.py migrate

# 5. Static files
python3 manage.py collectstatic --noinput

# 6. Transfer reference genomes
rsync -avz dev-machine:/path/to/reference_genomes/ /home/ubuntu/apps/rnaseek/reference_genomes/

# 7. Create media dir
mkdir -p /home/ubuntu/apps/rnaseek/media/sessions

# 8. Systemd services
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now rnaseek-web rnaseek-worker rnaseek-beat

# 9. Nginx + SSL
sudo ln -sf /home/ubuntu/apps/rnaseek/nginx/rnaseek.conf /etc/nginx/sites-enabled/rnaseek.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo certbot --nginx -d your-domain.com
sudo systemctl reload nginx

# 10. Verify
python3 manage.py check --deploy
curl -I https://your-domain.com
```
