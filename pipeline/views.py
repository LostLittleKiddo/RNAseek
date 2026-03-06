import json
import os
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import TemplateView

from pipeline.models import AnalysisJob, FileAsset, Session
from pipeline.tasks import run_core_pipeline


# ── Page Views ─────────────────────────────────────────────


class HomeView(TemplateView):
    template_name = "pipeline/home.html"


class TutorialsView(TemplateView):
    template_name = "pipeline/tutorials.html"


class WorkspacesView(TemplateView):
    template_name = "pipeline/workspaces.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        ctx["jobs"] = list(
            session_obj.analysis_jobs.values(
                "job_id", "module_name", "status"
            )
        )
        return ctx


class NewSubmissionView(TemplateView):
    template_name = "pipeline/new_submission.html"
    nav_step = 1

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["nav_step"] = self.nav_step
        return ctx


class ProcessingView(TemplateView):
    template_name = "pipeline/processing.html"
    nav_step = 2

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        job = get_object_or_404(
            AnalysisJob, job_id=kwargs["job_id"], session=session_obj
        )
        ctx["job"] = job
        ctx["job_id"] = str(job.job_id)
        ctx["nav_step"] = self.nav_step
        return ctx


class CoreHubView(TemplateView):
    template_name = "pipeline/core_hub.html"
    nav_step = 3

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        job = get_object_or_404(
            AnalysisJob, job_id=kwargs["job_id"], session=session_obj
        )
        ctx["job"] = job
        ctx["job_id"] = str(job.job_id)
        ctx["has_h5ad"] = session_obj.file_assets.filter(
            file_role=FileAsset.FileRole.H5AD_PSEUDO
        ).exists()
        ctx["nav_step"] = self.nav_step
        return ctx


class AdvancedView(TemplateView):
    template_name = "pipeline/advanced.html"
    nav_step = 4

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        job = get_object_or_404(
            AnalysisJob, job_id=kwargs["job_id"], session=session_obj
        )
        ctx["job"] = job
        ctx["job_id"] = str(job.job_id)
        ctx["nav_step"] = self.nav_step
        return ctx


class ChunkUploadView(View):
    """Receive a 5 MB chunk of a FASTQ upload and append it to a temp file.

    Expected multipart fields:
        file        – binary chunk
        filename    – original file name
        chunk_index – 0-based chunk ordinal
        total_chunks – total number of chunks
    """

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj
        uploaded = request.FILES.get("file")
        filename = request.POST.get("filename", "")
        chunk_index = int(request.POST.get("chunk_index", 0))
        total_chunks = int(request.POST.get("total_chunks", 1))

        if not uploaded or not filename:
            return JsonResponse({"error": "Missing file or filename."}, status=400)

        # Sanitise the filename to prevent path traversal
        safe_name = os.path.basename(filename)
        dest_dir = os.path.join(
            settings.MEDIA_ROOT,
            "sessions",
            str(session_obj.session_id),
            "raw",
        )
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, safe_name)

        mode = "ab" if chunk_index > 0 else "wb"
        with open(dest_path, mode) as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        is_last = chunk_index + 1 >= total_chunks
        if is_last:
            FileAsset.objects.create(
                session=session_obj,
                file_role=FileAsset.FileRole.RAW_FASTQ,
                local_path=dest_path,
                is_user_uploaded=True,
            )

        return JsonResponse({
            "status": "ok",
            "chunk": chunk_index,
            "complete": is_last,
        })


class CorePipelineView(View):
    """Trigger the Core Pipeline as a Celery background task."""

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj

        body = json.loads(request.body) if request.body else {}

        job = AnalysisJob.objects.create(
            session=session_obj,
            module_name="CORE_PIPELINE",
        )
        run_core_pipeline.apply_async(
            args=[str(session_obj.session_id)],
            task_id=str(job.job_id),
        )

        return JsonResponse({
            "job_id": str(job.job_id),
            "status": job.status,
        })


class JobStatusView(View):
    """Poll the status of an AnalysisJob by its UUID."""

    http_method_names = ["get"]

    def get(self, request, job_id):
        session_obj = request.session_obj
        try:
            job = AnalysisJob.objects.get(
                job_id=job_id, session=session_obj
            )
        except AnalysisJob.DoesNotExist:
            return JsonResponse({"error": "Job not found."}, status=404)

        data = {
            "job_id": str(job.job_id),
            "module_name": job.module_name,
            "status": job.status,
        }
        if job.status == AnalysisJob.Status.SUCCESS:
            data["payload"] = job.result_payload
        return JsonResponse(data)


class SessionAssetsView(View):
    """Return the list of FileAsset roles for the current session."""

    http_method_names = ["get"]

    def get(self, request):
        session_obj = request.session_obj
        assets = list(
            session_obj.file_assets.values("id", "file_role", "local_path", "is_user_uploaded")
        )
        return JsonResponse({"assets": assets})
