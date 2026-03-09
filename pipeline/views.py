import json
import os
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View
from django.views.generic import TemplateView

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset, Session
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
        ctx["genome_choices"] = [
            {"group": "Vertebrates", "options": [
                {"value": "hg38", "label": "Human (GRCh38 / hg38)"},
                {"value": "mm39", "label": "Mouse (GRCm39 / mm39)"},
                {"value": "mm10", "label": "Mouse (GRCm38 / mm10)"},
                {"value": "rn7", "label": "Rat (mRatBN7.2 / rn7)"},
                {"value": "danRer11", "label": "Zebrafish (GRCz11 / danRer11)"},
                {"value": "galGal6", "label": "Chicken (GRCg6a / galGal6)"},
                {"value": "susScr11", "label": "Pig (Sscrofa11.1 / susScr11)"},
            ]},
            {"group": "Invertebrates", "options": [
                {"value": "dm6", "label": "Drosophila (BDGP6 / dm6)"},
                {"value": "wbcel235", "label": "C. elegans (WBcel235)"},
            ]},
            {"group": "Other Organisms", "options": [
                {"value": "r64", "label": "Yeast (R64-1-1 / sacCer3)"},
                {"value": "araTha", "label": "Arabidopsis (TAIR10)"},
            ]},
        ]
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


class CreateSubmissionView(View):
    """Create a new AnalysisSubmission and return its UUID.

    The frontend calls this on page load to get a submission_id
    that scopes all subsequent uploads to a dedicated folder.
    """

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj
        submission = AnalysisSubmission.objects.create(session=session_obj)
        os.makedirs(submission.upload_dir, exist_ok=True)
        return JsonResponse({
            "submission_id": str(submission.submission_id),
        })


class ChunkUploadView(View):
    """Receive a 5 MB chunk of a FASTQ upload and append it to a temp file.

    Expected multipart fields:
        file           – binary chunk
        filename       – original file name
        chunk_index    – 0-based chunk ordinal
        total_chunks   – total number of chunks
        submission_id  – UUID of the AnalysisSubmission
        file_role      – FileAsset role (default: RAW_FASTQ)
    """

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj
        uploaded = request.FILES.get("file")
        filename = request.POST.get("filename", "")
        chunk_index = int(request.POST.get("chunk_index", 0))
        total_chunks = int(request.POST.get("total_chunks", 1))
        file_role = request.POST.get("file_role", FileAsset.FileRole.RAW_FASTQ)
        submission_id = request.POST.get("submission_id", "")

        if not uploaded or not filename:
            return JsonResponse({"error": "Missing file or filename."}, status=400)

        if not submission_id:
            return JsonResponse({"error": "Missing submission_id."}, status=400)

        # Validate submission belongs to this session
        try:
            submission = AnalysisSubmission.objects.get(
                submission_id=submission_id, session=session_obj
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse({"error": "Invalid submission."}, status=400)

        # Validate file_role against allowed choices
        valid_roles = {choice[0] for choice in FileAsset.FileRole.choices}
        if file_role not in valid_roles:
            return JsonResponse({"error": "Invalid file role."}, status=400)

        # Sanitise the filename to prevent path traversal
        safe_name = os.path.basename(filename)

        # Route files to subdirectories within the submission folder
        if file_role in (
            FileAsset.FileRole.CUSTOM_GENOME_FASTA,
            FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
        ):
            subdir = "custom_genome"
        elif file_role == FileAsset.FileRole.METADATA_CSV:
            subdir = "metadata"
        elif file_role == FileAsset.FileRole.ALIGNMENT_BAM:
            subdir = "aligned"
        elif file_role == FileAsset.FileRole.USER_COUNT_MATRIX:
            subdir = "counts"
        else:
            subdir = "raw"

        dest_dir = os.path.join(submission.upload_dir, subdir)
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
                submission=submission,
                file_role=file_role,
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

    VALID_LIBRARY_TYPES = {"single", "paired"}
    VALID_STRANDEDNESS = {"unstranded", "fr-firststrand", "fr-secondstrand"}
    VALID_QUANT_LEVELS = {"gene", "transcript"}
    VALID_INPUT_DATA_TYPES = {"fastq", "alignment", "matrix"}

    def post(self, request):
        session_obj = request.session_obj
        body = json.loads(request.body) if request.body else {}

        submission_id = body.get("submission_id")
        if not submission_id:
            return JsonResponse({"error": "Missing submission_id."}, status=400)

        try:
            submission = AnalysisSubmission.objects.get(
                submission_id=submission_id, session=session_obj
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse({"error": "Invalid submission."}, status=400)

        # ── Determine entry point ──
        input_data_type = body.get("input_data_type", "fastq")
        if input_data_type not in self.VALID_INPUT_DATA_TYPES:
            return JsonResponse({"error": "Invalid input_data_type."}, status=400)

        # ── Fields only required for FASTQ entry ──
        library_type = body.get("library_type", "")
        strandedness = body.get("strandedness", "unstranded")

        if input_data_type == "fastq":
            if library_type not in self.VALID_LIBRARY_TYPES:
                return JsonResponse({"error": "Invalid library_type."}, status=400)
            if strandedness not in self.VALID_STRANDEDNESS:
                return JsonResponse({"error": "Invalid strandedness."}, status=400)

        # ── Reference genome: required for fastq and alignment, not for matrix ──
        reference_genome = body.get("reference_genome", "")
        quant_level = body.get("quant_level", "gene")

        if input_data_type in ("fastq", "alignment"):
            if not reference_genome:
                return JsonResponse({"error": "Reference genome is required."}, status=400)
            if quant_level not in self.VALID_QUANT_LEVELS:
                return JsonResponse({"error": "Invalid quant_level."}, status=400)

        metadata_mode = body.get("metadata_mode", "")
        if metadata_mode not in ("upload", "manual"):
            return JsonResponse({"error": "Invalid metadata_mode."}, status=400)

        # ── Validate input files based on entry point ──
        if input_data_type == "fastq":
            fastq_assets = submission.file_assets.filter(
                file_role=FileAsset.FileRole.RAW_FASTQ
            )
            if not fastq_assets.exists():
                return JsonResponse({"error": "No FASTQ files uploaded."}, status=400)
            if library_type == "paired" and fastq_assets.count() % 2 != 0:
                return JsonResponse(
                    {"error": "Paired-end requires an even number of FASTQ files."},
                    status=400,
                )

        elif input_data_type == "alignment":
            bam_assets = submission.file_assets.filter(
                file_role=FileAsset.FileRole.ALIGNMENT_BAM
            )
            if not bam_assets.exists():
                return JsonResponse({"error": "No BAM/CRAM files uploaded."}, status=400)

        elif input_data_type == "matrix":
            matrix_assets = submission.file_assets.filter(
                file_role=FileAsset.FileRole.USER_COUNT_MATRIX
            )
            if not matrix_assets.exists():
                return JsonResponse({"error": "No count matrix uploaded."}, status=400)

        # ── Validate custom genome files ──
        if input_data_type in ("fastq", "alignment") and reference_genome == "custom":
            custom_name = body.get("custom_genome_name", "").strip()
            if not custom_name:
                return JsonResponse({"error": "Custom genome name is required."}, status=400)

            if input_data_type == "fastq":
                # Full pipeline needs both FASTA (for HISAT2 index) and GTF
                has_fasta = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
                ).exists()
                has_annotation = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
                ).exists()
                if not has_fasta or not has_annotation:
                    return JsonResponse(
                        {"error": "Custom genome requires both FASTA and GTF/GFF files."},
                        status=400,
                    )
            else:
                # Alignment entry only needs GTF for featureCounts
                has_annotation = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
                ).exists()
                if not has_annotation:
                    return JsonResponse(
                        {"error": "Custom genome requires a GTF/GFF annotation file."},
                        status=400,
                    )

        # ── Validate metadata ──
        meta = body.get("metadata_payload", {})

        if metadata_mode == "upload":
            has_csv = submission.file_assets.filter(
                file_role=FileAsset.FileRole.METADATA_CSV
            ).exists()
            if not has_csv:
                return JsonResponse({"error": "No metadata CSV uploaded."}, status=400)
        elif metadata_mode == "manual":
            samples = meta.get("samples", [])
            if not samples:
                return JsonResponse(
                    {"error": "Manual metadata requires sample data."},
                    status=400,
                )

        # ── Validate column mapping ──
        col_mapping = meta.get("column_mapping", {})
        primary_group = col_mapping.get("primary_group")
        if not primary_group:
            return JsonResponse(
                {"error": "A primary group column must be selected."},
                status=400,
            )

        # Validate contrasts (if provided)
        contrasts = meta.get("contrasts", [])
        for pair in contrasts:
            if not isinstance(pair, list) or len(pair) != 2:
                return JsonResponse(
                    {"error": "Each contrast must be a [target, reference] pair."},
                    status=400,
                )
            if pair[0] == pair[1]:
                return JsonResponse(
                    {"error": "Contrast target and reference must be different."},
                    status=400,
                )

        # ── Validate thresholds ──
        try:
            adj_pvalue = float(body.get("adjusted_pvalue", 0.05))
            min_log2fc = float(body.get("min_log2fc", -1.0))
            max_log2fc = float(body.get("max_log2fc", 1.0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid threshold values."}, status=400)

        if not (0 < adj_pvalue <= 1):
            return JsonResponse({"error": "adjusted_pvalue must be between 0 and 1."}, status=400)

        # ── Persist the full payload on the submission ──
        submission.input_data_type = input_data_type
        submission.library_type = library_type
        submission.strandedness = strandedness
        submission.reference_genome = reference_genome
        submission.custom_genome_name = body.get("custom_genome_name", "")
        submission.metadata_mode = metadata_mode
        submission.adjusted_pvalue = adj_pvalue
        submission.min_log2fc = min_log2fc
        submission.max_log2fc = max_log2fc
        submission.metadata_payload = body.get("metadata_payload", {})
        # Store quant_level in the payload for the Celery task
        if "quant_level" not in submission.metadata_payload:
            submission.metadata_payload["quant_level"] = quant_level
        submission.save()

        job = AnalysisJob.objects.create(
            session=session_obj,
            module_name="CORE_PIPELINE",
        )
        run_core_pipeline.apply_async(
            args=[str(session_obj.session_id), str(submission.submission_id)],
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
