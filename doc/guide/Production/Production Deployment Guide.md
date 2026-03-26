# RNAseek -- Production Deployment Guide

> Bare-metal deployment of RNAseek on Ubuntu 22.04+ with PostgreSQL, Redis, Nginx, and systemd.

---

## Prerequisites

| Component      | Version     | Purpose                                        |
| -------------- | ----------- | ---------------------------------------------- |
| Ubuntu         | 22.04+      | Host operating system                          |
| PostgreSQL     | 15+         | Production database                            |
| Redis          | 7+          | Celery broker, Channels layer, result backend  |
| Nginx          | Latest      | Reverse proxy, SSL termination, static serving |
| Certbot        | Latest      | Let's Encrypt SSL certificate provisioning     |
| Miniconda3     | Latest      | Python/R/bioinformatics tool environment       |
| tusd           | v2          | Tus protocol resumable upload daemon           |

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

## 4. Nginx Configuration

### 4.1 Symlink and Enable

```bash
sudo ln -sf /home/ubuntu/apps/rnaseek/nginx/rnaseek.conf /etc/nginx/sites-enabled/rnaseek.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

### 4.2 Key Nginx Settings

The production Nginx config (`nginx/rnaseek.conf`) handles:

| Location     | Upstream                   | Purpose                                    |
| ------------ | -------------------------- | ------------------------------------------ |
| `/`          | `127.0.0.1:8000` (Daphne) | All Django HTTP requests                   |
| `/ws/`       | `127.0.0.1:8000` (Daphne) | WebSocket upgrade for real-time progress   |
| `/files/`    | `127.0.0.1:1080` (tusd)   | Tus resumable uploads (streaming proxy)    |
| `/api/upload/` | `127.0.0.1:8000` (Daphne) | Legacy chunked upload endpoint           |
| `/static/`   | Filesystem                 | Direct file serving with 30-day cache      |

`client_max_body_size` is set to `0` (unlimited) because tusd handles chunking. `proxy_request_buffering` is disabled on `/files/` so Nginx streams data directly to tusd without local disk buffering.

### 4.3 SSL Certificates

```bash
sudo certbot --nginx -d rnaseek.ca -d www.rnaseek.ca
```

Certbot auto-renews via its systemd timer. Verify with:

```bash
sudo systemctl status certbot.timer
```

---

## 5. tusd Installation

tusd handles resumable file uploads via the Tus protocol. Install it as a standalone binary:

```bash
# Download tusd v2 binary
wget https://github.com/tus/tusd/releases/download/v2.9.2/tusd_linux_amd64.tar.gz
tar xzf tusd_linux_amd64.tar.gz
sudo mv tusd_linux_amd64/tusd /usr/local/bin/tusd
sudo chmod +x /usr/local/bin/tusd
```

### 5.1 Create a systemd Service for tusd

```bash
sudo tee /etc/systemd/system/rnaseek-tusd.service > /dev/null <<'EOF'
[Unit]
Description=RNAseek tusd Upload Daemon
After=network.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
ExecStart=/usr/local/bin/tusd \
    -host 127.0.0.1 \
    -port 1080 \
    -upload-dir /home/ubuntu/apps/rnaseek/media/uploads \
    -base-path /files/ \
    -hooks-http http://127.0.0.1:8000/api/tusd-hooks/ \
    -hooks-http-forward-headers Cookie,X-Session-ID \
    -hooks-enabled-events post-finish \
    -behind-proxy \
    -max-size 0

Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable rnaseek-tusd
sudo systemctl start rnaseek-tusd
```

### 5.2 Verify tusd is Running

```bash
sudo systemctl status rnaseek-tusd
curl -s http://127.0.0.1:1080/files/ -I | head -5
```

---

## 6. systemd Services

### 6.1 Install Service Files

```bash
sudo cp /home/ubuntu/apps/rnaseek/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 6.2 Service Overview

| Service              | Binary                 | Purpose                         |
| -------------------- | ---------------------- | ------------------------------- |
| `rnaseek-web`        | Daphne (ASGI)          | HTTP + WebSocket on port 8000   |
| `rnaseek-worker`     | Celery worker (prefork)| Pipeline execution              |
| `rnaseek-beat`       | Celery Beat            | Scheduled task dispatch         |
| `rnaseek-tusd`       | tusd v2                | Resumable uploads on port 1080  |

All services use the Conda environment at `/opt/miniconda3/envs/rnaseek` and read from `/home/ubuntu/apps/rnaseek/.env`.

### 6.3 Enable and Start

```bash
for svc in rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd; do
    sudo systemctl enable "$svc"
    sudo systemctl restart "$svc"
    echo "$svc: $(sudo systemctl is-active $svc)"
done
```

---

## 7. Deployment Script

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

## 8. NFS Shared Storage (Multi-Server)

If running workers on separate machines, all servers must mount the same NFS export at the media path.

### 8.1 NFS Server Export

```bash
# On the NFS server:
echo "/exports/rnaseek *(rw,sync,no_subtree_check,no_root_squash)" | sudo tee -a /etc/exports
sudo exportfs -ra
```

### 8.2 NFS Client Mount

```bash
sudo mount -t nfs4 <NFS_SERVER_IP>:/exports/rnaseek /home/ubuntu/apps/rnaseek/media \
    -o rw,nfsvers=4.1,async,noatime,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2
```

Add to `/etc/fstab` for persistence:

```
<NFS_SERVER_IP>:/exports/rnaseek /home/ubuntu/apps/rnaseek/media nfs4 rw,nfsvers=4.1,async,noatime,rsize=1048576,wsize=1048576,hard,timeo=600,retrans=2 0 0
```

### 8.3 NFS Mount Flags

| Flag              | Purpose                                              |
| ----------------- | ---------------------------------------------------- |
| `async`           | Client ACKs writes before server confirms (throughput)|
| `noatime`         | Skip access-time updates on reads (reduces I/O)      |
| `rsize=1048576`   | 1 MB read blocks (matches tusd/upload chunk pattern)  |
| `wsize=1048576`   | 1 MB write blocks                                    |
| `hard`            | Retry indefinitely on timeout (prevents data loss)   |
| `timeo=600`       | 60s timeout before retry                             |
| `retrans=2`       | Two retries before marking hard error                |

---

## 9. Upload Benchmarking

Run the built-in benchmark script to measure server upload capacity:

```bash
bash scripts/benchmark-upload-capacity.sh
```

This tests:
- Network bandwidth (iperf3 or curl fallback)
- Sequential disk write speed (dd with direct I/O)
- Parallel disk write throughput (fio, if installed)

---

## 10. Health Checks

### 10.1 Service Status

```bash
sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd
```

### 10.2 Application Logs

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

### 10.3 Database Check

```bash
cd /home/ubuntu/apps/rnaseek
python manage.py check --deploy
```

### 10.4 Redis Check

```bash
redis-cli ping
# Expected: PONG
```

---

## 11. Maintenance

### 11.1 Manual Session Purge

```bash
# Dry run (see what would be deleted):
python manage.py purge_expired --dry-run

# Execute purge:
python manage.py purge_expired
```

The automated purge runs daily at 2:00 AM UTC via Celery Beat.

### 11.2 Database Backup

```bash
pg_dump -U rnaseek -h 127.0.0.1 rnaseek > backup_$(date +%Y%m%d).sql
```

### 11.3 SSL Certificate Renewal

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
      |
      +-- /files/     --> tusd (127.0.0.1:1080)  --> NFS media volume
      +-- /ws/        --> Daphne (127.0.0.1:8000) --> Redis Channels
      +-- /static/    --> filesystem (staticfiles/)
      +-- /*          --> Daphne (127.0.0.1:8000)
                             |
                             v
                          Redis 7+
                             |
                    +--------+--------+
                    |                 |
              Celery Worker     Celery Beat
              (pipeline)        (nightly purge)
                    |
                    v
              NFS /media/
```
