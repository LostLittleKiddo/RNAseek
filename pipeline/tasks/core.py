"""Celery task entry points: core pipeline router and nightly janitor."""

import logging
import shutil

from celery import shared_task

from pipeline.tasks._helpers import _emit_progress

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


@shared_task(bind=True)
def run_tier2_module(self, session_id, core_job_id, module_name, params=None):
    """Tier 2 module runner: dispatches the named analytical module.

    Pre-conditions (checked by the API view before dispatch):
      - module_name is one of the 12 approved modules
      - The core pipeline job (core_job_id) completed with SUCCESS
    """
    from pipeline.models import AnalysisJob, AnalysisSubmission

    job = AnalysisJob.objects.get(job_id=self.request.id)
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    params = params or {}

    try:
        core_job = AnalysisJob.objects.get(job_id=core_job_id)
        session = core_job.session

        # Locate the submission from the core job's session
        submission = (
            session.submissions.order_by("-created_at").first()
        )
        if not submission:
            raise RuntimeError("No submission found for this session.")

        _emit_progress(job)

        # Dispatch to the correct module engine.
        if module_name == "WGCNA":
            result = _dispatch_wgcna(
                job, session_id, str(submission.submission_id), **params,
            )
        elif module_name == "RNA_EDITING":
            result = _dispatch_rna_editing(
                job, session_id, str(submission.submission_id), **params,
            )
        elif module_name == "TIME_SERIES":
            result = _dispatch_timeseries(
                job, session_id, str(submission.submission_id), **params,
            )
        else:
            # Placeholder for unimplemented modules.
            progress = job.step_progress or {}
            progress["current_step"] = module_name
            job.step_progress = progress
            job.save(update_fields=["step_progress"])
            _emit_progress(job)

            result = {
                "module": module_name,
                "message": f"Module {module_name} executed successfully.",
                "params": params,
            }

        # Mark completed — refresh to pick up step_progress changes from engine.
        job.refresh_from_db(fields=["step_progress"])
        progress = job.step_progress or {}
        progress["completed_steps"] = list(progress.get("pipeline_steps", []))
        progress["current_step"] = None
        job.step_progress = progress
        job.status = AnalysisJob.Status.SUCCESS
        job.result_payload = result
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)

    except Exception as exc:
        logger.exception("Tier 2 module %s failed", module_name)
        job.refresh_from_db(fields=["step_progress"])
        progress = job.step_progress or {}
        progress["failed_step"] = progress.get("current_step") or module_name
        progress["current_step"] = None
        job.step_progress = progress
        job.status = AnalysisJob.Status.FAILED
        job.result_payload = {"error": str(exc)}
        job.save(update_fields=["status", "result_payload", "step_progress"])
        _emit_progress(job)
        raise

    return {"job_id": str(job.job_id), "status": job.status}


# ---------------------------------------------------------------------------
# Module-specific dispatchers (private)
# ---------------------------------------------------------------------------

def _dispatch_wgcna(job, session_id, submission_id, **kwargs):
    """Resolve input files and delegate to the WGCNA + pathway enrichment engine.

    Required FileAsset roles on the submission:
        NORMALIZED_COUNTS  - DESeq2 normalized count matrix
        METADATA_CSV       - sample-level metadata (conditions / traits)
    """
    from pipeline.models import AnalysisSubmission, FileAsset
    from pipeline.tasks._module_wgcna import execute_wgcna_and_pathways

    submission = AnalysisSubmission.objects.get(
        submission_id=submission_id, session_id=session_id,
    )

    matrix_asset = FileAsset.objects.get(
        session_id=session_id,
        submission=submission,
        file_role=FileAsset.FileRole.NORMALIZED_COUNTS,
    )
    metadata_asset = FileAsset.objects.get(
        session_id=session_id,
        submission=submission,
        file_role=FileAsset.FileRole.METADATA_CSV,
    )

    return execute_wgcna_and_pathways(
        job_id=str(job.job_id),
        session_id=str(session_id),
        matrix_path=matrix_asset.local_path,
        metadata_path=metadata_asset.local_path,
        **kwargs,
    )


def _dispatch_rna_editing(job, session_id, submission_id, **kwargs):
    """Resolve BAM files and genome FASTA, then delegate to the RNA editing engine.

    Required FileAsset roles on the submission:
        ALIGNMENT_BAM  - one or more aligned BAM files from the core pipeline

    The reference genome FASTA is resolved from the submission's
    ``reference_genome`` field via ``_genome_paths()``.

    Optional params (forwarded from the frontend form):
        whole_transcriptome : bool
        bed_data            : str   (raw BED file content)
    """
    import os
    import tempfile

    from pipeline.models import AnalysisSubmission, FileAsset
    from pipeline.tasks._genome import _genome_paths, _resolve_genome
    from pipeline.tasks._module_rna_editing import execute_rna_editing

    submission = AnalysisSubmission.objects.get(
        submission_id=submission_id, session_id=session_id,
    )

    # Collect all BAM files produced by the core pipeline for this submission.
    bam_assets = FileAsset.objects.filter(
        session_id=session_id,
        submission=submission,
        file_role=FileAsset.FileRole.ALIGNMENT_BAM,
    )
    bam_paths = [a.local_path for a in bam_assets if os.path.isfile(a.local_path)]
    if not bam_paths:
        raise FileNotFoundError(
            "No aligned BAM files found for this submission. "
            "Run the core pipeline first."
        )

    # Resolve the reference genome FASTA.
    genome_key = submission.reference_genome
    _, genome_fasta, _ = _resolve_genome(
        genome_key, submission.upload_dir, submission=submission,
    )
    if not genome_fasta:
        raise FileNotFoundError(
            f"Could not resolve reference FASTA for genome '{genome_key}'."
        )

    # If the user provided BED data as text, write it to a temporary file.
    whole_transcriptome = kwargs.pop("whole_transcriptome", False)
    bed_data = kwargs.pop("bed_data", None)
    bed_path = None

    if not whole_transcriptome and bed_data and isinstance(bed_data, str):
        work_dir = os.path.join(submission.upload_dir, "rna_editing")
        os.makedirs(work_dir, exist_ok=True)
        bed_path = os.path.join(work_dir, "target_regions.bed")
        with open(bed_path, "w") as fh:
            fh.write(bed_data)

    return execute_rna_editing(
        job_id=str(job.job_id),
        session_id=str(session_id),
        bam_paths=bam_paths,
        genome_fasta=genome_fasta,
        bed_path=bed_path,
        whole_transcriptome=whole_transcriptome,
    )


def _dispatch_timeseries(job, session_id, submission_id, **kwargs):
    """Resolve normalized count matrix and delegate to the Time Series engine.

    Required FileAsset roles on the submission:
        NORMALIZED_COUNTS  - DESeq2 normalized count matrix

    Required params (from the frontend form):
        mapping_data : dict   (sample -> timepoint / condition mapping)
        time_unit    : str    (minutes, hours, days, weeks)
    """
    from pipeline.models import AnalysisSubmission, FileAsset
    from pipeline.tasks._module_timeseries import execute_timeseries

    submission = AnalysisSubmission.objects.get(
        submission_id=submission_id, session_id=session_id,
    )

    matrix_asset = FileAsset.objects.get(
        session_id=session_id,
        submission=submission,
        file_role=FileAsset.FileRole.NORMALIZED_COUNTS,
    )

    mapping_data = kwargs.pop("mapping_data", None)
    if not mapping_data:
        raise ValueError("No sample mapping data provided.")

    time_unit = kwargs.pop("time_unit", "hours")

    return execute_timeseries(
        job_id=str(job.job_id),
        session_id=str(session_id),
        matrix_path=matrix_asset.local_path,
        mapping_data=mapping_data,
        time_unit=time_unit,
    )


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
