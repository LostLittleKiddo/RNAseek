import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("rnaseek")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Celery Beat schedule — nightly janitor at 2:00 AM UTC
app.conf.beat_schedule = {
    "purge-expired-sessions": {
        "task": "pipeline.tasks.purge_expired_sessions",
        "schedule": crontab(hour=2, minute=0),
    },
}
