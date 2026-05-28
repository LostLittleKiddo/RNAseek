# RNAseek -- Production Deployment Guide

> Bare-metal deployment of RNAseek on Ubuntu 22.04+ with PostgreSQL, Redis, Nginx, and systemd.

---

## Prerequisites

| Component  | Version | Purpose                                        |
| ---------- | ------- | ---------------------------------------------- |
| Ubuntu     | 22.04+  | Host operating system                          |
| PostgreSQL | 15+     | Production database                            |
| Redis      | 7+      | Celery broker, Channels layer, result backend  |
| Nginx      | Latest  | Reverse proxy, SSL termination, static serving |
| Certbot    | Latest  | Let's Encrypt SSL certificate provisioning     |
| Miniconda3 | Latest  | Python/R/bioinformatics tool environment       |
| tusd       | v2      | Tus protocol resumable upload daemon           |

---

## 1. System Setup

### 1.1 Install System Packages

```bash
sudo apt-get update && sudo apt-get install -y \
    build-essential libcurl4-openssl-dev libssl-dev libxml2-dev \
    libhdf5-dev pkg-config procps nginx certbot python3-certbot-nginx \
    postgresql postgresql-contrib redis-server
```

### 1.2 Install Miniconda

```bash
wget https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh
bash Miniconda3-latest-Linux-x86_64.sh -b -p /opt/miniconda3
```

### 1.3 Create Conda Environment

```bash
conda env create -n rnaseek -f /home/ubuntu/apps/rnaseek/environment.yml
conda activate rnaseek
pip install -r /home/ubuntu/apps/rnaseek/requirements.txt
```

---

## 2. PostgreSQL Setup

```bash
sudo -u postgres psql <<EOF
CREATE USER rnaseek WITH PASSWORD '<STRONG_PASSWORD>';
CREATE DATABASE rnaseek OWNER rnaseek;
ALTER USER rnaseek CREATEDB;   -- needed for test runner
EOF
```

---

## 3. Application Configuration

### 3.1 Create the Environment File

```bash
cd /home/ubuntu/apps/rnaseek
cp .env.production .env
```

Edit `.env` with production values:

```
DJANGO_SECRET_KEY=<generate-a-64-char-random-string>
RNASEEK_ENV=production
DB_NAME=rnaseek
DB_USER=rnaseek
DB_PASSWORD=<your-postgres-password>
DB_HOST=127.0.0.1
DB_PORT=5432
REDIS_URL=redis://127.0.0.1:6379/0
CELERY_BROKER_URL=redis://127.0.0.1:6379/0
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/0
MEDIA_ROOT=/home/ubuntu/apps/rnaseek/media
DJANGO_ALLOWED_HOSTS=rnaseek.ca,www.rnaseek.ca,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://rnaseek.ca,https://www.rnaseek.ca
```

`127.0.0.1` must be in `DJANGO_ALLOWED_HOSTS` because tusd sends its webhook POST to `http://127.0.0.1:8000/api/tusd-hooks/`.

If `SECURE_SSL_REDIRECT` is enabled, add an exemption in `settings.py` so the internal HTTP webhook is not redirected to HTTPS:

```python
SECURE_REDIRECT_EXEMPT = [r"^api/tusd-hooks/"]
```

### 3.2 Run Migrations and Collect Static

```bash
conda activate rnaseek
cd /home/ubuntu/apps/rnaseek
python manage.py migrate --noinput
python manage.py collectstatic --noinput
```

---

## 4. Set Up Reference Genomes

The pipeline requires pre-built genome indices in `pipeline/reference_genomes/`. This is a one-time step. The scripts are in `doc/script/` and must be run from inside the `reference_genomes` directory with the conda environment active. Expect several hundred GB of disk space and multiple hours of build time.

```bash
conda activate rnaseek
cd /home/ubuntu/apps/rnaseek/pipeline/reference_genomes
```

### 4.1 Download Genome FASTA Files

Downloads and extracts FASTA files for all 11 supported species. Each genome is placed in `<Species>/genome/`. Pass `-c` flag allows resuming interrupted downloads.

```bash
bash ../../doc/script/download_genomes.sh
```

### 4.2 Build HISAT2 Indices (RNA-seq)

```bash
bash ../../doc/script/build_hisat2_indices.sh
```

### 4.3 Build BWA Indices (ChIP-seq)

Runs up to 15 indexing jobs in parallel.

```bash
bash ../../doc/script/build_bwa_indices.sh
```

### 4.4 Build Bismark Indices (DNA Methylation)

Runs up to 15 jobs in parallel; allow ~5 GB RAM per concurrent job.

```bash
bash ../../doc/script/build_bismark_indices.sh
```

### 4.5 Build miRBase Bowtie Indices (Small RNA)

Downloads `mature.fa` from miRBase and builds per-species Bowtie indices under `miRBase/`.

```bash
bash ../../doc/script/build_mirbase_indices.sh
```

```bash
cd /home/ubuntu/apps/rnaseek   # return to project root
```

---

## 5. Nginx Configuration

### 5.1 Symlink and Enable

```bash
sudo ln -sf /home/ubuntu/apps/rnaseek/nginx/rnaseek.conf /etc/nginx/sites-enabled/rnaseek.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 5.2 Key Nginx Settings

The production Nginx config (`nginx/rnaseek.conf`) handles:

| Location       | Upstream                           | Purpose                                  |
| -------------- | ---------------------------------- | ---------------------------------------- |
| `/`            | `127.0.0.1:8000` (Daphne)          | All Django HTTP requests                 |
| `/ws/`         | `127.0.0.1:8000` (Daphne)          | WebSocket upgrade for real-time progress |
| `/files/`      | `tusd_backend` (keepalive 64 pool) | Tus resumable uploads (streaming proxy)  |
| `/api/upload/` | `127.0.0.1:8000` (Daphne)          | Legacy chunked upload endpoint           |
| `/static/`     | Filesystem                         | Direct file serving with 30-day cache    |

**Upload optimizations:**
- `upstream tusd_backend` with `keepalive 64` — reuses connections between Nginx ↔ tusd
- `proxy_request_buffering off` — streams data directly to tusd without local disk buffering
- `proxy_set_header Upload-Concat` — forwards the tus Concatenation header for parallel chunk uploads
- `proxy_set_header Connection ""` — required for upstream keepalive pool
- `proxy_socket_keepalive on` — detects dead connections during long uploads
- `client_max_body_size 0` — unlimited (tusd handles chunking)

**TCP tuning (server-level):**
- `sendfile on` — kernel-space file copy for static files
- `tcp_nopush on` — coalesces headers + body into fewer packets
- `tcp_nodelay on` — disables Nagle’s algorithm for low-latency streaming

### 5.3 SSL Certificates

```bash
sudo certbot --nginx -d rnaseek.ca -d www.rnaseek.ca
```

Certbot auto-renews via its systemd timer. Verify with:

```bash
sudo systemctl status certbot.timer
```

---

## 6. tusd Installation

tusd handles resumable file uploads via the Tus protocol. Install it as a standalone binary:

```bash
# Download tusd v2 binary
wget https://github.com/tus/tusd/releases/download/v2.9.2/tusd_linux_amd64.tar.gz
tar xzf tusd_linux_amd64.tar.gz
sudo mv tusd_linux_amd64/tusd /usr/local/bin/tusd
sudo chmod +x /usr/local/bin/tusd
```

### 6.1 Create a systemd Service for tusd

The tusd service file is maintained in the repository at `systemd/rnaseek-tusd.service`.
Key flags:
- `-disable-download` — blocks file retrieval via tusd (files are served by Django)
- `-max-size 0` — no upload size limit
- `-behind-proxy` — trusts `X-Forwarded-*` headers from Nginx
- `-hooks-enabled-events post-finish` — only fires webhook after upload completes
- `LimitNOFILE=65536` — supports many concurrent upload connections

The Concatenation extension is enabled by default in tusd v2 with the file store,
allowing `tus-js-client` / Uppy.js `parallelUploads` to split a single file into
multiple parts uploaded simultaneously.

```bash
sudo cp systemd/rnaseek-tusd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable rnaseek-tusd
sudo systemctl start rnaseek-tusd
```

### 6.2 Verify tusd is Running

```bash
sudo systemctl status rnaseek-tusd
curl -s http://127.0.0.1:1080/files/ -I | head -5
```

---

## 7. systemd Services

### 7.1 Install Service Files

```bash
sudo cp /home/ubuntu/apps/rnaseek/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 7.2 Service Overview

| Service              | Binary                  | Purpose                                                                                         |
| -------------------- | ----------------------- | ----------------------------------------------------------------------------------------------- |
| `rnaseek-web`        | Daphne (ASGI)           | HTTP + WebSocket on port 8000                                                                   |
| `rnaseek-celery-cpu` | Celery worker (prefork) | CPU-bound pipeline execution (`cpu_bound` queue, concurrency=5, 8 threads/tool, 40 cores)       |
| `rnaseek-celery-ram` | Celery worker (prefork) | RAM-bound analytics execution (`ram_bound` queue, concurrency=4, MemoryHigh=88G, MemoryMax=96G) |
| `rnaseek-beat`       | Celery Beat             | Scheduled task dispatch                                                                         |
| `rnaseek-tusd`       | tusd v2                 | Resumable uploads on port 1080, `-disable-download`, `LimitNOFILE=65536`                        |

**Legacy:** `rnaseek-worker.service` (single worker) has been replaced by the two asymmetric workers above. Disable it if still enabled: `sudo systemctl disable rnaseek-worker`.

All services use the Conda environment at `/opt/miniconda3/envs/rnaseek` and read from `/home/ubuntu/apps/rnaseek/.env`.

### 7.3 Resource Allocation (48-core / 128 GB RAM)

| Component                        | Cores | RAM Budget                                |
| -------------------------------- | ----- | ----------------------------------------- |
| CPU worker (5 procs × 8 threads) | 40    | ~20 GB                                    |
| RAM worker (4 procs × ~1 thread) | 4     | ≤96 GB (hard capped by systemd MemoryMax) |
| OS + Nginx + Redis + Daphne      | 4     | ~12 GB                                    |

### 7.4 Enable and Start

```bash
for svc in rnaseek-web rnaseek-celery-cpu rnaseek-celery-ram rnaseek-beat rnaseek-tusd; do
    sudo systemctl enable "$svc"
    sudo systemctl restart "$svc"
    echo "$svc: $(sudo systemctl is-active $svc)"
done
```

---

## 8. Deployment Script

For subsequent deployments after initial setup, use the automated deploy script:

```bash
cd /home/ubuntu/apps/rnaseek
bash deploy.sh
```

This script:

1. Validates the `.env` file exists
2. Installs/updates pip dependencies
3. Runs database migrations
4. Collects static files
5. Symlinks and reloads Nginx
6. Restarts all systemd services

---

## 9. NFS Shared Storage (Multi-Server)

If running workers on separate machines, all servers must mount the same NFS export at the media path.

### 9.1 NFS Server Export

```bash
# On the NFS server:
echo "/exports/rnaseek *(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
sudo exportfs -ra
```

### 9.2 NFS Client Mount

```bash
sudo mount -t nfs4 <NFS_SERVER_IP>:/exports/rnaseek /home/ubuntu/apps/rnaseek/media \
    -o rw,nfsvers=4.1,async,noatime,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2
```

Add to `/etc/fstab` for persistence:

```
<NFS_SERVER_IP>:/exports/rnaseek /home/ubuntu/apps/rnaseek/media nfs4 rw,nfsvers=4.1,async,noatime,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 0 0
```

### 9.3 NFS Mount Flags

| Flag            | Purpose                                                |
| --------------- | ------------------------------------------------------ |
| `async`         | Client ACKs writes before server confirms (throughput) |
| `noatime`       | Skip access-time updates on reads (reduces I/O)        |
| `rsize=1048576` | 1 MB read blocks (matches tusd/upload chunk pattern)   |
| `wsize=1048576` | 1 MB write blocks                                      |
| `hard`          | Retry indefinitely on timeout (prevents data loss)     |
| `timeo=600`     | 60s timeout before retry                               |
| `retrans=2`     | Two retries before marking hard error                  |

---

## 10. Upload Benchmarking

Run the built-in benchmark script to measure server upload capacity:

```bash
bash scripts/benchmark-upload-capacity.sh
```

This tests:
- Network bandwidth (iperf3 or curl fallback)
- Sequential disk write speed (dd with direct I/O)
- Parallel disk write throughput (fio, if installed)

---

## 11. Health Checks

### 11.1 Service Status

```bash
sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd
```

### 11.2 Application Logs

```bash
# Web server logs
sudo journalctl -u rnaseek-web -f

# Worker logs (pipeline execution)
sudo journalctl -u rnaseek-worker -f

# Beat scheduler logs
sudo journalctl -u rnaseek-beat -f

# tusd upload logs
sudo journalctl -u rnaseek-tusd -f
```

### 11.3 Database Check

```bash
cd /home/ubuntu/apps/rnaseek
python manage.py check --deploy
```

### 11.4 Redis Check

```bash
redis-cli ping
# Expected: PONG
```

---

## 12. Maintenance

### 12.1 Manual Session Purge

```bash
# Dry run (see what would be deleted):
python manage.py purge_expired --dry-run

# Execute purge:
python manage.py purge_expired
```

The automated purge runs daily at 2:00 AM UTC via Celery Beat.

### 12.2 Database Backup

```bash
pg_dump -U rnaseek -h 127.0.0.1 rnaseek > backup_$(date +%Y%m%d).sql
```

### 12.3 SSL Certificate Renewal

Certbot auto-renews. Force a renewal test with:

```bash
sudo certbot renew --dry-run
```

---

## Architecture Summary

```
  Client (HTTPS)
      |
      v
  Nginx (port 443)
  [sendfile on; tcp_nopush on; tcp_nodelay on]
  [upstream tusd_backend: keepalive 64]
      |
      +-- /files/     --> tusd (127.0.0.1:1080)  --> NVMe media volume
      +-- /ws/        --> Daphne (127.0.0.1:8000) --> Redis Channels
      +-- /static/    --> filesystem (staticfiles/)
      +-- /*          --> Daphne (127.0.0.1:8000)
                             |
                             v
                          Redis 7+
                             |
                    +--------+--------+--------+
                    |                 |        |
            CPU Worker         RAM Worker   Celery Beat
        (cpu_bound queue)  (ram_bound queue) (nightly purge)
         concurrency=5      concurrency=4
         8 threads/tool     MemoryMax=96G
                    |                 |
                    v                 v
              NVMe /media/      NVMe /media/
```

### OS-Level Tuning

The production server is tuned via `scripts/tune-production-os.sh` (run once on initial setup):

| Tuning                 | Value                       | Purpose                                                |
| ---------------------- | --------------------------- | ------------------------------------------------------ |
| TCP congestion         | BBR                         | Maximizes WAN upload throughput for 50 GB+ FASTQ files |
| TCP buffers            | 128 MB max per-socket       | Covers 10 Gbps × 100 ms BDP                            |
| `vm.overcommit_memory` | 1                           | Prevents R `fork()` failures                           |
| `vm.swappiness`        | 10                          | Keeps R datasets in RAM                                |
| File descriptors       | 2M system / 1M per-user     | Supports concurrent uploads + workers                  |
| User ulimits           | nofile=1048576, nproc=65535 | For the `ubuntu` service user                          |
