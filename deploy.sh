#!/usr/bin/env bash
# ============================================================
# RNAseek — Production Deployment Script
# ============================================================
# Run once on a fresh server, then use the "update" section
# for subsequent deployments.
#
# Prerequisites:
#   - Ubuntu 22.04+ with sudo
#   - Conda environment "rnaseek" (see environment.yml)
#   - PostgreSQL 15+ installed and running
#   - Redis 7+ installed and running
#   - Nginx installed
#   - Certbot SSL certs already provisioned for rnaseek.ca
# ============================================================

set -euo pipefail

APP_DIR="/home/ubuntu/apps/rnaseek"
CONDA_ENV="rnaseek"
DB_NAME="rnaseek"
DB_USER="rnaseek"

echo "=== RNAseek Production Deployment ==="

# ── 1. Environment file ──
if [ ! -f "$APP_DIR/.env" ]; then
    echo "ERROR: .env file not found. Copy and edit .env.production first:"
    echo "  cp $APP_DIR/.env.production $APP_DIR/.env"
    echo "  # Edit .env with real DB_PASSWORD and DJANGO_SECRET_KEY"
    exit 1
fi

cd "$APP_DIR"

# ── 2. Install/update pip dependencies ──
echo "=== Installing pip dependencies ==="
pip3 install --quiet -r requirements.txt

# ── 3. Run migrations ──
echo "=== Running database migrations ==="
python3 manage.py migrate --noinput

# ── 4. Collect static files ──
echo "=== Collecting static files ==="
python3 manage.py collectstatic --noinput

# ── 5. Nginx config ──
echo "=== Setting up Nginx ==="
if [ ! -L /etc/nginx/sites-enabled/rnaseek.conf ]; then
    sudo ln -sf "$APP_DIR/nginx/rnaseek.conf" /etc/nginx/sites-enabled/rnaseek.conf
    # Remove default site if it exists
    sudo rm -f /etc/nginx/sites-enabled/default
fi
sudo nginx -t
sudo systemctl reload nginx

# ── 6. Systemd services ──
echo "=== Setting up systemd services ==="
sudo cp "$APP_DIR/systemd/"*.service /etc/systemd/system/ 2>/dev/null || true
sudo systemctl daemon-reload

for svc in rnaseek-web rnaseek-worker rnaseek-beat; do
    sudo systemctl enable "$svc"
    sudo systemctl restart "$svc"
    echo "  $svc: $(sudo systemctl is-active $svc)"
done

echo ""
echo "=== Deployment complete ==="
echo "  Web:    https://rnaseek.ca"
echo "  Status: sudo systemctl status rnaseek-web rnaseek-worker rnaseek-beat"
echo "  Logs:   sudo journalctl -u rnaseek-web -f"
