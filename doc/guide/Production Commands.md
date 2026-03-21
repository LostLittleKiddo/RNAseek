# RNAseek — Production Commands

> **Server:** rnaseek.ca (bare metal Ubuntu, no Docker)  
> **App dir:** `/home/ubuntu/apps/rnaseek`  
> **Conda env:** `/opt/miniconda3/envs/rnaseek`  
> **Database:** PostgreSQL 14 — db `rnaseek`, user `rnaseek`

---

## Services

RNAseek runs as three systemd services behind Nginx:

| Service | What it does |
|---|---|
| `rnaseek-web` | Daphne ASGI server (HTTP + WebSocket) on `127.0.0.1:8000` |
| `rnaseek-worker` | Celery worker — runs bioinformatics pipelines |
| `rnaseek-beat` | Celery beat — scheduled tasks (session cleanup at 2 AM UTC) |
| `nginx` | Reverse proxy with SSL (Let's Encrypt) |

---

## Everyday Commands

### Check status of all services

```bash
sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat nginx
```

### Start / Stop / Restart individual services

```bash
sudo systemctl start   rnaseek-web
sudo systemctl stop    rnaseek-web
sudo systemctl restart rnaseek-web
```

Replace `rnaseek-web` with `rnaseek-worker`, `rnaseek-beat`, or `nginx`.

### Restart everything

```bash
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat nginx
```

### Stop everything

```bash
sudo systemctl stop rnaseek-web rnaseek-worker rnaseek-beat
```

---

## Logs

### View live logs (follow mode)

```bash
sudo journalctl -u rnaseek-web -f
sudo journalctl -u rnaseek-worker -f
sudo journalctl -u rnaseek-beat -f
```

### View recent logs (last 100 lines)

```bash
sudo journalctl -u rnaseek-web -n 100 --no-pager
sudo journalctl -u rnaseek-worker -n 100 --no-pager
```

### View logs since a specific time

```bash
sudo journalctl -u rnaseek-worker --since "2026-03-21 00:00:00" --no-pager
```

### Nginx logs

```bash
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

---

## Deployment (Code Updates)

After pulling new code from git:

```bash
cd /home/ubuntu/apps/rnaseek

# Activate conda environment
export PATH=/opt/miniconda3/envs/rnaseek/bin:$PATH

# Install any new Python packages
pip install -r requirements.txt

# Run database migrations
python manage.py migrate --noinput

# Collect static files
python manage.py collectstatic --noinput

# Restart services to pick up code changes
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat
```

Or use the deploy script (does all of the above):

```bash
cd /home/ubuntu/apps/rnaseek && bash deploy.sh
```

---

## Database

### Django management shell

```bash
cd /home/ubuntu/apps/rnaseek
export PATH=/opt/miniconda3/envs/rnaseek/bin:$PATH
set -a && . .env && set +a
python manage.py shell
```

### Run migrations

```bash
python manage.py migrate --noinput
```

### Connect to PostgreSQL directly

```bash
sudo -u postgres psql -d rnaseek
```

### Full database reset (destructive — drops all data)

```bash
# 1. Stop services
sudo systemctl stop rnaseek-web rnaseek-worker rnaseek-beat

# 2. Drop and recreate database
sudo -u postgres psql -c "DROP DATABASE IF EXISTS rnaseek;"
sudo -u postgres psql -c "CREATE DATABASE rnaseek OWNER rnaseek;"

# 3. Re-run migrations
cd /home/ubuntu/apps/rnaseek
export PATH=/opt/miniconda3/envs/rnaseek/bin:$PATH
set -a && . .env && set +a
python manage.py migrate --noinput

# 4. Collect static files
python manage.py collectstatic --noinput

# 5. Clean celery beat schedule
rm -f celerybeat-schedule.dat celerybeat-schedule.bak celerybeat-schedule.dir

# 6. Restart services
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat
```

---

## Celery

### Purge all pending tasks from the queue

```bash
cd /home/ubuntu/apps/rnaseek
export PATH=/opt/miniconda3/envs/rnaseek/bin:$PATH
set -a && . .env && set +a
celery -A config purge -f
```

### Inspect active tasks

```bash
celery -A config inspect active
```

### Inspect registered tasks

```bash
celery -A config inspect registered
```

---

## Nginx

### Test config before reloading

```bash
sudo nginx -t
```

### Reload (no downtime)

```bash
sudo systemctl reload nginx
```

### Restart

```bash
sudo systemctl restart nginx
```

---

## SSL Certificate (Let's Encrypt)

### Check certificate expiry

```bash
sudo certbot certificates
```

### Manually renew

```bash
sudo certbot renew
sudo systemctl reload nginx
```

Auto-renewal is handled by the certbot systemd timer.

---

## Systemd Service Files

The service unit files live in the repo and are copied to systemd:

```
/home/ubuntu/apps/rnaseek/systemd/rnaseek-web.service
/home/ubuntu/apps/rnaseek/systemd/rnaseek-worker.service
/home/ubuntu/apps/rnaseek/systemd/rnaseek-beat.service
```

After editing a service file:

```bash
sudo cp /home/ubuntu/apps/rnaseek/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat
```

---

## Quick Health Check

```bash
# All services running?
sudo systemctl is-active rnaseek-web rnaseek-worker rnaseek-beat nginx

# Site responding?
curl -s -o /dev/null -w "%{http_code}\n" https://rnaseek.ca/

# Redis alive?
redis-cli ping

# PostgreSQL alive?
sudo systemctl is-active postgresql
```

---

## Environment

The `.env` file at `/home/ubuntu/apps/rnaseek/.env` contains secrets (DB password, Django secret key, etc.). Never commit it to git.

Key environment variables:

| Variable | Purpose |
|---|---|
| `RNASEEK_ENV` | Must be `production` |
| `DJANGO_SECRET_KEY` | Django secret key |
| `DB_NAME` / `DB_USER` / `DB_PASSWORD` | PostgreSQL credentials |
| `DJANGO_ALLOWED_HOSTS` | `rnaseek.ca,www.rnaseek.ca` |
| `CSRF_TRUSTED_ORIGINS` | `https://rnaseek.ca,https://www.rnaseek.ca` |
| `REDIS_URL` | Default `redis://127.0.0.1:6379/0` |
| `MEDIA_ROOT` | Where uploaded files and pipeline outputs go |
