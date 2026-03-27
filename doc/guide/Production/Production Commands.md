# RNAseek -- Production Commands

> Quick reference for common production server operations.

---

## Service Management

```bash
# Status of all RNAseek services
sudo systemctl status rnaseek-web rnaseek-celery-cpu rnaseek-celery-ram rnaseek-beat rnaseek-tusd

# Restart a specific service
sudo systemctl restart rnaseek-web
sudo systemctl restart rnaseek-celery-cpu
sudo systemctl restart rnaseek-celery-ram
sudo systemctl restart rnaseek-beat
sudo systemctl restart rnaseek-tusd

# Restart all services
sudo systemctl restart rnaseek-web rnaseek-celery-cpu rnaseek-celery-ram rnaseek-beat rnaseek-tusd

# Stop all services
sudo systemctl stop rnaseek-web rnaseek-celery-cpu rnaseek-celery-ram rnaseek-beat rnaseek-tusd 2>/dev/null || true
```

> **Note:** The legacy `rnaseek-worker.service` has been replaced by `rnaseek-celery-cpu` and `rnaseek-celery-ram`.

---

## Logs

```bash
# Live web server logs
sudo journalctl -u rnaseek-web -f

# Live CPU worker logs (genome alignment execution)
sudo journalctl -u rnaseek-celery-cpu -f

# Live RAM worker logs (R analytics: DESeq2, WGCNA, rMATS)
sudo journalctl -u rnaseek-celery-ram -f

# Live Beat scheduler logs
sudo journalctl -u rnaseek-beat -f

# Live tusd upload daemon logs
sudo journalctl -u rnaseek-tusd -f

# Last 100 lines from a service
sudo journalctl -u rnaseek-celery-cpu -n 100

# Logs from today only
sudo journalctl -u rnaseek-web --since today
```

---

## Deployment

```bash
cd /home/ubuntu/apps/rnaseek

# 1. Apply OS tuning (one-time)
sudo bash scripts/tune-production-os.sh

# 2. Install new systemd units
sudo cp systemd/rnaseek-celery-cpu.service /etc/systemd/system/
sudo cp systemd/rnaseek-celery-ram.service /etc/systemd/system/
sudo cp systemd/rnaseek-tusd.service /etc/systemd/system/
sudo systemctl daemon-reload

# 3. Stop old worker, start new split workers
sudo systemctl stop rnaseek-worker
sudo systemctl disable rnaseek-worker
sudo systemctl enable --now rnaseek-celery-cpu rnaseek-celery-ram

# 4. Restart tusd with new flags
sudo systemctl restart rnaseek-tusd

# 5. Reload Nginx (zero-downtime)
sudo nginx -t && sudo systemctl reload nginx

# 6. Collect static files (JS changes)
/opt/miniconda3/envs/rnaseek/bin/python manage.py collectstatic --noinput
```

---

## Database

```bash
# Django deployment check
cd /home/ubuntu/apps/rnaseek
python manage.py check --deploy

# Run migrations
python manage.py migrate

# Open Django shell
python manage.py shell

# Database backup
pg_dump -U rnaseek -h 127.0.0.1 rnaseek > backup_$(date +%Y%m%d).sql

# Restore from backup
psql -U rnaseek -h 127.0.0.1 rnaseek < backup_YYYYMMDD.sql
```

---

## Session Management

```bash
# Dry run: see what would be purged
python manage.py purge_expired --dry-run

# Execute purge (deletes expired sessions, files, and DB rows)
python manage.py purge_expired
```

Automated purge runs daily at 2:00 AM UTC via Celery Beat.

---

## Nginx

```bash
# Test config syntax
sudo nginx -t

# Reload (no downtime)
sudo systemctl reload nginx

# View Nginx error log
sudo tail -100 /var/log/nginx/error.log

# SSL certificate renewal test
sudo certbot renew --dry-run
```

---

## Redis

```bash
# Check Redis is alive
redis-cli ping

# Monitor real-time commands
redis-cli monitor

# Check memory usage
redis-cli info memory | grep used_memory_human
```

---

## Celery

```bash
# Inspect active workers and their queues
celery -A config inspect active_queues

# Inspect active tasks
celery -A config inspect active

# Inspect registered tasks
celery -A config inspect registered

# Inspect scheduled tasks (Beat)
celery -A config inspect scheduled

# Check queue lengths in Redis
redis-cli llen cpu_bound
redis-cli llen ram_bound
redis-cli llen celery   # should be 0 (legacy queue)

# Purge all pending tasks (destructive)
celery -A config purge
```

---

## Upload Benchmarking

```bash
# Run the built-in server capacity benchmark
bash scripts/benchmark-upload-capacity.sh
```

Tests network bandwidth (iperf3/curl), sequential disk I/O (dd), and parallel write throughput (fio).

---

## Troubleshooting

### Worker not picking up tasks

```bash
# Check workers are connected to Redis and subscribed to correct queues
celery -A config inspect active_queues

# Check queue lengths (tasks should be in cpu_bound or ram_bound, NOT celery)
redis-cli llen cpu_bound
redis-cli llen ram_bound
redis-cli llen celery   # if > 0, Daphne may need restart to pick up new CELERY_TASK_ROUTES

# If tasks are stuck in the legacy 'celery' queue after a routing change,
# restart Daphne so it loads the new CELERY_TASK_ROUTES from settings.py:
sudo systemctl restart rnaseek-web
```

### RAM worker OOM-killed

```bash
# Check if systemd killed the RAM worker for exceeding MemoryMax (96G)
sudo journalctl -u rnaseek-celery-ram --since today | grep -i oom

# The service auto-restarts (OOMPolicy=stop + Restart=always).
# If persistent, reduce concurrency in rnaseek-celery-ram.service.
```

### tusd not accepting uploads

```bash
# Check tusd is listening
curl -s http://127.0.0.1:1080/files/ -I | head -5

# Check Nginx is proxying to tusd
curl -sk https://rnaseek.ca/files/ -I | head -5

# Check tusd logs
sudo journalctl -u rnaseek-tusd -n 50
```

### WebSocket not connecting

```bash
# Verify Daphne is running
curl -s http://127.0.0.1:8000/ -I | head -3

# Check Nginx WebSocket config
sudo nginx -t
```
