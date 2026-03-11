import json
import mimetypes
import os
import uuid

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
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
            session_obj.analysis_jobs.order_by("-created_at").values(
                "job_id", "module_name", "status", "created_at"
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

    PIPELINE_STEPS = [
        {"key": "hisat2_build", "title": "Build HISAT2 Index", "desc": "Building genome index from custom FASTA (may take a while)"},
        {"key": "fastqc", "title": "FastQC", "desc": "Quality control on raw reads"},
        {"key": "trimmomatic", "title": "Trimmomatic", "desc": "Adapter trimming & quality filtering"},
        {"key": "hisat2", "title": "HISAT2 Alignment", "desc": "Splice-aware alignment to reference genome"},
        {"key": "featurecounts", "title": "featureCounts", "desc": "Gene-level read quantification"},
        {"key": "multiqc", "title": "MultiQC Report", "desc": "Aggregate all QC logs into a single report"},
        {"key": "deseq2", "title": "Batch Correction & DESeq2", "desc": "Combat-seq normalization & differential expression testing"},
    ]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        job = get_object_or_404(
            AnalysisJob, job_id=kwargs["job_id"], session=session_obj
        )
        ctx["job"] = job
        ctx["job_id"] = str(job.job_id)
        ctx["nav_step"] = self.nav_step

        # Only show the steps that were actually selected for this job
        active_keys = set((job.step_progress or {}).get("pipeline_steps", []))
        ctx["pipeline_steps"] = [
            s for s in self.PIPELINE_STEPS if s["key"] in active_keys
        ]
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

        samples = meta.get("samples", [])
        if not samples:
            return JsonResponse(
                {"error": "Metadata requires sample data."},
                status=400,
            )

        # Validate that the first column of uploaded metadata is "sample"
        if metadata_mode == "upload" and samples and isinstance(samples[0], dict):
            first_col = list(samples[0].keys())[0] if samples[0] else ""
            if first_col.strip().lower() != "sample":
                return JsonResponse(
                    {"error": "The first column of metadata must be named 'sample'."},
                    status=400,
                )

        # ── Validate metadata sample names match uploaded files ──
        if metadata_mode == "upload" and samples and isinstance(samples[0], dict):
            import re as re_mod

            sample_col = list(samples[0].keys())[0]
            meta_sample_ids = {
                (row.get(sample_col) or "").strip() for row in samples
            }
            meta_sample_ids.discard("")

            # Build the set of expected sample stems from uploaded files
            expected_stems = set()
            if input_data_type == "fastq":
                fastq_assets = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.RAW_FASTQ
                )
                fq_names = [os.path.basename(p) for p in fastq_assets.values_list("local_path", flat=True)]
                if library_type == "paired":
                    pair_re = re_mod.compile(r'^(.+?)(?:_R[12]|_[12])\.(?:fq|fastq)\.gz$', re_mod.IGNORECASE)
                    for name in fq_names:
                        m = pair_re.match(name)
                        if m:
                            expected_stems.add(m.group(1))
                else:
                    for name in fq_names:
                        stem = re_mod.sub(r'\.(fq|fastq)(\.gz)?$', '', name, flags=re_mod.IGNORECASE)
                        expected_stems.add(stem)
            elif input_data_type == "alignment":
                bam_assets = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.ALIGNMENT_BAM
                )
                for p in bam_assets.values_list("local_path", flat=True):
                    stem = re_mod.sub(r'\.(bam|cram)$', '', os.path.basename(p), flags=re_mod.IGNORECASE)
                    expected_stems.add(stem)

            if expected_stems:
                unmatched = expected_stems - meta_sample_ids
                if unmatched:
                    return JsonResponse(
                        {"error": f"Metadata is missing rows for uploaded samples: {', '.join(sorted(unmatched))}. "
                                  f"The 'sample' column must contain the filename stem (without extension)."},
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

        # Determine pipeline steps based on entry point
        if input_data_type == "fastq":
            steps = ["fastqc", "trimmomatic", "hisat2", "featurecounts", "multiqc", "deseq2"]
            if reference_genome == "custom":
                steps.insert(0, "hisat2_build")
        elif input_data_type == "alignment":
            steps = ["featurecounts", "deseq2"]
        else:
            steps = ["deseq2"]

        job = AnalysisJob.objects.create(
            session=session_obj,
            module_name="CORE_PIPELINE",
            step_progress={
                "pipeline_steps": steps,
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

        try:
            run_core_pipeline.apply_async(
                args=[str(session_obj.session_id), str(submission.submission_id)],
                task_id=str(job.job_id),
            )
        except Exception:
            # In eager mode the task may fail synchronously; job status
            # is already updated by the task's own except handler.
            # In async mode, a broker connection error means the task
            # never queued — mark the job as failed.
            job.refresh_from_db()
            if job.status not in (AnalysisJob.Status.SUCCESS, AnalysisJob.Status.FAILED):
                job.status = AnalysisJob.Status.FAILED
                job.result_payload = {"error": "Task queue unavailable. Is the Celery worker running?"}
                job.save(update_fields=["status", "result_payload"])

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

        # Detect stale RUNNING jobs (Celery task lost/revoked)
        if job.status == AnalysisJob.Status.RUNNING:
            try:
                from celery.result import AsyncResult
                result = AsyncResult(str(job.job_id))
                if result.state in ("REVOKED",):
                    job.status = AnalysisJob.Status.FAILED
                    job.result_payload = {"error": "Task was cancelled."}
                    progress = job.step_progress or {}
                    progress["failed_step"] = progress.get("current_step")
                    progress["current_step"] = None
                    job.step_progress = progress
                    job.save(update_fields=["status", "result_payload", "step_progress"])
            except Exception:
                pass

        data = {
            "job_id": str(job.job_id),
            "module_name": job.module_name,
            "status": job.status,
            "step_progress": job.step_progress,
        }
        if job.status == AnalysisJob.Status.SUCCESS:
            data["payload"] = job.result_payload
        elif job.status == AnalysisJob.Status.FAILED:
            data["error"] = (job.result_payload or {}).get("error", "Unknown error.")
        return JsonResponse(data)


class SessionAssetsView(View):
    """Return assets for the current session, optionally filtered by role.

    Query params:
        role    – FileAsset.FileRole value to filter by
        job_id  – AnalysisJob UUID; when combined with role, redirects to the
                  first matching asset's download URL for convenience.
    """

    http_method_names = ["get"]

    def get(self, request):
        from django.shortcuts import redirect

        session_obj = request.session_obj
        role = request.GET.get("role")
        job_id = request.GET.get("job_id")

        qs = session_obj.file_assets.all()
        if role:
            qs = qs.filter(file_role=role)
        if job_id:
            # Scope to assets belonging to the submission linked to this job
            try:
                job = AnalysisJob.objects.get(job_id=job_id, session=session_obj)
            except (AnalysisJob.DoesNotExist, ValueError):
                return JsonResponse({"error": "Job not found."}, status=404)
            # Find submissions for this session and filter assets
            qs = qs.filter(submission__in=session_obj.submissions.all())

        asset = qs.first()
        if asset and role:
            # Redirect directly to the download endpoint
            from django.urls import reverse
            return redirect(reverse("file_download", args=[asset.id]))

        assets = list(qs.values("id", "file_role", "local_path", "is_user_uploaded"))
        return JsonResponse({"assets": assets})


class FileDownloadView(View):
    """Serve a FileAsset for download, restricted to the owning session.

    Security: The file must belong to the requesting user's session_id cookie.
    The local_path is validated to reside under MEDIA_ROOT to prevent traversal.
    Uses Django's FileResponse for efficient streaming of large files.
    """

    http_method_names = ["get"]

    def get(self, request, asset_id):
        session_obj = request.session_obj

        try:
            asset = FileAsset.objects.get(id=asset_id, session=session_obj)
        except (FileAsset.DoesNotExist, ValueError):
            raise Http404("File not found.")

        file_path = asset.local_path

        # Prevent path traversal: resolved path must be under MEDIA_ROOT
        media_root = str(settings.MEDIA_ROOT)
        resolved = os.path.realpath(file_path)
        if not resolved.startswith(os.path.realpath(media_root)):
            raise Http404("File not found.")

        if not os.path.isfile(resolved):
            raise Http404("File not found on disk.")

        content_type, _ = mimetypes.guess_type(resolved)
        if not content_type:
            content_type = "application/octet-stream"

        response = FileResponse(
            open(resolved, "rb"),
            content_type=content_type,
            as_attachment=True,
            filename=os.path.basename(resolved),
        )
        return response
