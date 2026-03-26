#!/usr/bin/env bash
# ============================================================
# RNAseek — Production Deploy Script
# ============================================================
# Called by Bitbucket Pipelines (via SSH) after git pull.
# Also usable manually: bash deploy.sh
# ============================================================
set -euo pipefail

CONDA_ENV="rnaseek"
APP_DIR="/home/ubuntu/apps/rnaseek"
PYTHON="/opt/miniconda3/envs/${CONDA_ENV}/bin/python"
PIP="/opt/miniconda3/envs/${CONDA_ENV}/bin/pip"

cd "$APP_DIR"

echo "==> Installing Python dependencies..."
"$PIP" install -r requirements.txt --quiet

echo "==> Running database migrations..."
"$PYTHON" manage.py migrate --no-input

echo "==> Collecting static files..."
"$PYTHON" manage.py collectstatic --no-input

echo "==> Restarting services..."
sudo systemctl restart rnaseek-web rnaseek-worker rnaseek-beat

echo "==> Waiting for services to start..."
sleep 3

echo "==> Service status:"
sudo systemctl is-active rnaseek-web rnaseek-worker rnaseek-beat

echo "==> Deploy complete!"
