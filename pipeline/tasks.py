from celery import shared_task


@shared_task(bind=True)
def run_core_pipeline(self, session_id):
    """Placeholder for the Core Pipeline Celery task.

    Bioinformatic logic (fastqc, hisat2, DESeq2, etc.) will be implemented
    in a later phase.  For now this task simply transitions through the
    PENDING -> RUNNING -> SUCCESS lifecycle so the frontend polling works
    end-to-end.
    """
    from pipeline.models import AnalysisJob

    job = AnalysisJob.objects.get(job_id=self.request.id)
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    # ------ bioinformatics work will go here ------

    job.status = AnalysisJob.Status.SUCCESS
    job.result_payload = {"message": "Core pipeline completed."}
    job.save(update_fields=["status", "result_payload"])
    return {"job_id": str(job.job_id), "status": job.status}
