# RNAseek -- Production Commands

> Quick reference for common production server operations.

---

## Service Management

```bash
# Status of all RNAseek services
sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd

# Restart a specific service
sudo systemctl restart rnaseek-web
sudo systemctl restart rnaseek-worker
sudo systemctl restart rnaseek-beat
sudo systemctl restart rnaseek-tusd

# Restart all services
for svc in rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd; do
    sudo systemctl restart "$svc"
done

# Stop all services
for svc in rnaseek-web rnaseek-worker rnaseek-beat rnaseek-tusd; do
    sudo systemctl stop "$svc"
done
```

---

## Logs

```bash
# Live web server logs
sudo journalctl -u rnaseek-web -f

# Live worker logs (pipeline execution)
sudo journalctl -u rnaseek-worker -f

# Live Beat scheduler logs
sudo journalctl -u rnaseek-beat -f

# Live tusd upload daemon logs
sudo journalctl -u rnaseek-tusd -f

# Last 100 lines from a service
sudo journalctl -u rnaseek-worker -n 100

# Logs from today only
sudo journalctl -u rnaseek-web --since today
```

---

## Deployment

```bash
cd /home/ubuntu/apps/rnaseek

# Full deploy (pip install, migrate, collectstatic, systemd reload)
bash deploy.sh

# Manual steps if needed:
conda activate rnaseek
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat
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
# Inspect active workers
celery -A config inspect active

# Inspect registered tasks
celery -A config inspect registered

# Inspect scheduled tasks (Beat)
celery -A config inspect scheduled

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
# Check worker is connected to Redis
celery -A config inspect ping

# Check queue length
redis-cli llen celery
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
