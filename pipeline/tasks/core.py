"""Celery task entry points: core pipeline router and nightly janitor."""

import logging
import shutil

from celery import shared_task

from pipeline.tasks._helpers import _emit_progress, _update_step

logger = logging.getLogger(__name__)


@shared_task(bind=True)
def run_core_pipeline(self, session_id, submission_id):
    """Core Pipeline router: dispatches to the correct entry point.

    Routes:
      - fastq:     Full pipeline (FastQC → Trim → Align → Quantify → Stage 2)
      - alignment:  Skip to featureCounts on uploaded BAMs → Stage 2
      - matrix:    Skip to Stage 2 stats with user-provided count matrix
    """
    from pipeline.models import AnalysisJob, AnalysisSubmission
    from pipeline.tasks import (
        _route_alignment,
        _route_chip_seq,
        _route_fastq,
        _route_matrix,
        _route_methylation,
        _route_small_rna,
    )

    job = AnalysisJob.objects.get(job_id=self.request.id)
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        submission = AnalysisSubmission.objects.get(submission_id=submission_id)
        input_type = submission.input_data_type

        if input_type == "fastq":
            assay = submission.assay_type
            if assay == "small_rna":
                result = _route_small_rna(submission, job)
            elif assay == "chip_seq":
                result = _route_chip_seq(submission, job)
            elif assay == "methylation":
                result = _route_methylation(submission, job)
            else:
                result = _route_fastq(submission, job)
        elif input_type == "alignment":
            result = _route_alignment(submission, job)
        elif input_type == "matrix":
            result = _route_matrix(submission, job)
        else:
            raise ValueError(f"Unknown input_data_type: {input_type}")

        # Mark all steps as completed
        job.refresh_from_db(fields=["step_progress"])
        progress = job.step_progress or {}
        progress["completed_steps"] = list(progress.get("pipeline_steps", []))
        progress["current_step"] = None
        job.step_progress = progress
        job.status = AnalysisJob.Status.SUCCESS
        job.result_payload = {"message": "Core pipeline completed.", **result}
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)

    except Exception as exc:
        logger.exception("Core pipeline failed for submission %s", submission_id)
        job.refresh_from_db(fields=["step_progress"])
        progress = job.step_progress or {}
        progress["failed_step"] = progress.get("current_step")
        progress["current_step"] = None
        job.step_progress = progress
        job.status = AnalysisJob.Status.FAILED
        job.result_payload = {"error": str(exc)}
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)
        raise

    return {"job_id": str(job.job_id), "status": job.status}


@shared_task(ignore_result=True)
def purge_expired_sessions():
    """Celery Beat task: purge expired anonymous sessions.

    Runs nightly at 2:00 AM (scheduled in config/celery.py).
    Deletes the NFS directory for each expired session, then
    cascade-deletes the DB row and all child records.
    """
    from django.conf import settings
    from django.utils import timezone

    from pipeline.models import Session

    now = timezone.now()
    expired = Session.objects.filter(expires_at__lt=now)
    count = expired.count()

    if count == 0:
        logger.info("purge_expired_sessions: no expired sessions.")
        return

    logger.info("purge_expired_sessions: purging %d expired session(s).", count)
    purged = 0

    for session in expired.iterator():
        session_dir = (
            settings.MEDIA_ROOT / "sessions" / str(session.session_id)
        )

        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
            except OSError:
                logger.exception(
                    "Failed to delete NFS dir: %s", session_dir
                )

        session.delete()
        purged += 1

    logger.info("purge_expired_sessions: purged %d session(s).", purged)
