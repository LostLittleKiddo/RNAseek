"""Auto-Purge Janitor: Delete expired sessions and their NFS data.

This management command is designed to run nightly via Celery Beat
(or cron) to reclaim disk space from expired anonymous sessions.

Workflow:
  1. Query all Session rows where expires_at < now.
  2. For each expired session, physically delete its NFS directory
     at /app/media/sessions/{uuid}/ using shutil.rmtree().
  3. Delete the Session row — Django CASCADE ensures all child rows
     (AnalysisSubmission, FileAsset, AnalysisJob) are wiped too.

Usage:
    python manage.py purge_expired [--dry-run]
"""

import logging
import shutil

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pipeline.models import Session

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Purge expired sessions: delete NFS files and cascade DB rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List expired sessions without deleting anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        now = timezone.now()

        expired = Session.objects.filter(expires_at__lt=now)
        count = expired.count()

        if count == 0:
            self.stdout.write(self.style.SUCCESS("No expired sessions found."))
            return

        self.stdout.write(f"Found {count} expired session(s).")

        purged = 0
        errors = 0

        for session in expired.iterator():
            session_dir = (
                settings.MEDIA_ROOT / "sessions" / str(session.session_id)
            )

            if dry_run:
                self.stdout.write(
                    f"  [DRY-RUN] Would purge {session.session_id} "
                    f"(expired {session.expires_at}) dir={session_dir}"
                )
                continue

            # Phase 1: Obliterate the NFS directory tree.
            # If the directory doesn't exist (already cleaned), skip silently.
            if session_dir.exists():
                try:
                    shutil.rmtree(session_dir)
                    logger.info(
                        "Deleted NFS directory: %s", session_dir
                    )
                except OSError:
                    logger.exception(
                        "Failed to delete NFS directory: %s", session_dir
                    )
                    errors += 1
                    # Continue to DB deletion — stale rows are worse than
                    # orphaned files, and the next run can retry the rmtree.

            # Phase 2: Cascade-delete the DB row.
            # This removes Session + AnalysisSubmission + FileAsset +
            # AnalysisJob in one atomic cascade.
            session.delete()
            purged += 1
            logger.info("Purged session: %s", session.session_id)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run complete. {count} session(s) would be purged.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Purge complete: {purged} deleted, {errors} filesystem error(s)."
                )
            )
