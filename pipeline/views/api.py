"""API views — REST endpoints for uploads, pipeline triggers, and data retrieval."""

import json
import logging
import mimetypes
import os
import re as re_mod
import shutil

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset
from pipeline.tasks import run_core_pipeline
from pipeline.tasks.core import run_tier2_module
from pipeline.validators import validate_pipeline_submission

# ── Upload buffering constants ──
UPLOAD_BUFFER_ROOT = os.environ.get("RNASEEK_UPLOAD_BUFFER", "/tmp/rnaseek_uploads")


def _subdir_for_role(file_role):
    """Map a FileAsset role to its submission subdirectory."""
    if file_role in (
        FileAsset.FileRole.CUSTOM_GENOME_FASTA,
        FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION,
    ):
        return "custom_genome"
    if file_role == FileAsset.FileRole.METADATA_CSV:
        return "metadata"
    if file_role == FileAsset.FileRole.ALIGNMENT_BAM:
        return "aligned"
    if file_role == FileAsset.FileRole.USER_COUNT_MATRIX:
        return "counts"
    return "raw"


def _merge_and_move(buffer_dir, total_chunks, dest_path):
    """Stitch ordered chunks from *buffer_dir*, move the result to *dest_path* on NFS."""
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    merged_path = os.path.join(buffer_dir, "_merged")
    with open(merged_path, "wb") as out:
        for i in range(total_chunks):
            chunk_path = os.path.join(buffer_dir, f"chunk_{i}")
            with open(chunk_path, "rb") as cf:
                shutil.copyfileobj(cf, out)
    shutil.move(merged_path, dest_path)
    shutil.rmtree(buffer_dir, ignore_errors=True)


class FileAssetDeleteView(View):
    """Delete a user-uploaded FileAsset and its file on disk."""

    http_method_names = ["delete"]

    def delete(self, request, asset_id):
        session_obj = request.session_obj
        try:
            asset = FileAsset.objects.get(
                id=asset_id, session=session_obj, is_user_uploaded=True
            )
        except (FileAsset.DoesNotExist, ValueError):
            return JsonResponse({"error": "Asset not found."}, status=404)

        # Remove the file from disk if it exists
        if asset.local_path and os.path.isfile(asset.local_path):
            os.remove(asset.local_path)

        asset.delete()
        return JsonResponse({"status": "deleted"})


class CreateSubmissionView(View):
    """Create a new AnalysisSubmission and return its UUID."""

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj
        submission = AnalysisSubmission.objects.create(session=session_obj)
        os.makedirs(submission.upload_dir, exist_ok=True)
        return JsonResponse({
            "submission_id": str(submission.submission_id),
        })


@method_decorator(csrf_exempt, name="dispatch")
class DeleteSubmissionView(View):
    """Delete an AnalysisSubmission, its file assets, and upload directory.

    Uses csrf_exempt because navigator.sendBeacon (fired on page unload)
    cannot attach custom headers.  Session ownership is verified via the
    HttpOnly Session_ID cookie (SameSite=Lax prevents cross-site POSTs).
    """

    http_method_names = ["post"]

    def post(self, request):
        session_obj = request.session_obj
        body = json.loads(request.body) if request.body else {}
        submission_id = body.get("submission_id", "")

        if not submission_id:
            return JsonResponse({"error": "Missing submission_id."}, status=400)

        try:
            submission = AnalysisSubmission.objects.get(
                submission_id=submission_id, session=session_obj
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse({"error": "Submission not found."}, status=404)

        # Delete files on disk for all user-uploaded assets
        for asset in submission.file_assets.filter(is_user_uploaded=True):
            if asset.local_path and os.path.isfile(asset.local_path):
                os.remove(asset.local_path)

        # Remove the upload directory
        upload_dir = submission.upload_dir
        if os.path.isdir(upload_dir):
            shutil.rmtree(upload_dir)

        # Cascade-delete the submission (also deletes FileAsset rows)
        submission.delete()

        return JsonResponse({"status": "deleted"})


class ChunkUploadView(View):
    """Receive a chunk of a file upload, buffer it to local SSD, and merge
    when all chunks have arrived.

    Chunks may arrive out of order and concurrently.  Each chunk is written
    to a temporary directory under ``UPLOAD_BUFFER_ROOT``.  When the final
    chunk lands, the chunks are stitched in order and the completed file is
    moved to the NFS-backed destination in a single ``shutil.move()``.

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

        # Validate FASTA file extension for custom genome uploads
        if file_role == FileAsset.FileRole.CUSTOM_GENOME_FASTA:
            lower_name = safe_name.lower()
            allowed = (".fa", ".fasta", ".fa.gz", ".fasta.gz", ".fa.zip", ".fasta.zip")
            if not lower_name.endswith(allowed):
                return JsonResponse(
                    {"error": "Only .fa, .fasta, .fa.gz, .fasta.gz, .fa.zip, or .fasta.zip files are accepted for reference FASTA."},
                    status=400,
                )

        # ── Buffer chunk to local SSD ──
        buffer_dir = os.path.join(
            UPLOAD_BUFFER_ROOT, f"{submission_id}_{safe_name}"
        )
        os.makedirs(buffer_dir, exist_ok=True)

        # Write to a .tmp file first, then atomically rename so the final
        # name only appears once the data is fully flushed to disk.
        tmp_path = os.path.join(buffer_dir, f"chunk_{chunk_index}.tmp")
        final_chunk_path = os.path.join(buffer_dir, f"chunk_{chunk_index}")
        with open(tmp_path, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)
        os.rename(tmp_path, final_chunk_path)

        # ── Check if all chunks have arrived ──
        received = sum(
            1
            for name in os.listdir(buffer_dir)
            if name.startswith("chunk_") and not name.endswith(".tmp")
        )
        is_complete = received >= total_chunks

        asset_id = None
        if is_complete:
            # Claim merge responsibility atomically (O_EXCL is POSIX-atomic)
            merge_marker = os.path.join(buffer_dir, "_merging")
            try:
                fd = os.open(merge_marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                # Another concurrent request already started the merge
                return JsonResponse(
                    {"status": "ok", "chunk": chunk_index, "complete": False}
                )

            # Merge chunks and move to NFS
            subdir = _subdir_for_role(file_role)
            dest_dir = os.path.join(submission.upload_dir, subdir)
            dest_path = os.path.join(dest_dir, safe_name)
            _merge_and_move(buffer_dir, total_chunks, dest_path)

            asset = FileAsset.objects.create(
                session=session_obj,
                submission=submission,
                file_role=file_role,
                local_path=dest_path,
                is_user_uploaded=True,
            )
            asset_id = str(asset.id)

        resp = {
            "status": "ok",
            "chunk": chunk_index,
            "complete": is_complete and asset_id is not None,
        }
        if asset_id:
            resp["asset_id"] = asset_id
        return JsonResponse(resp)


class CorePipelineView(View):
    """Trigger the Core Pipeline as a Celery background task."""

    http_method_names = ["post"]

    VALID_LIBRARY_TYPES = {"single", "paired"}
    VALID_STRANDEDNESS = {"unstranded", "fr-firststrand", "fr-secondstrand"}
    VALID_QUANT_LEVELS = {"gene", "transcript"}
    VALID_INPUT_DATA_TYPES = {"fastq", "alignment", "matrix", "tcga"}
    VALID_ASSAY_TYPES = {"standard_rna", "small_rna", "chip_seq", "methylation"}

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

        # ── Run all validators ──
        errors, warnings = validate_pipeline_submission(body, submission)
        if errors:
            return JsonResponse(
                {"error": errors[0], "errors": errors}, status=400
            )

        # ── Extract validated fields ──
        input_data_type = body.get("input_data_type", "fastq")
        assay_type = body.get("assay_type", "standard_rna")
        library_type = body.get("library_type", "")
        strandedness = body.get("strandedness", "unstranded")
        reference_genome = body.get("reference_genome", "")
        quant_level = body.get("quant_level", "gene")
        metadata_mode = body.get("metadata_mode", "")
        adj_pvalue = float(body.get("adjusted_pvalue", 0.05))
        min_log2fc = float(body.get("min_log2fc", -1.0))
        max_log2fc = float(body.get("max_log2fc", 1.0))

        # ── Persist payload ──
        submission.submission_name = body.get("submission_name", "")[:200]
        submission.input_data_type = input_data_type
        submission.library_type = library_type
        submission.strandedness = strandedness
        submission.reference_genome = reference_genome
        submission.custom_genome_name = body.get("custom_genome_name", "")
        submission.metadata_mode = metadata_mode
        submission.adjusted_pvalue = adj_pvalue
        submission.min_log2fc = min_log2fc
        submission.max_log2fc = max_log2fc
        submission.assay_type = assay_type if input_data_type == "fastq" else "standard_rna"
        submission.metadata_payload = body.get("metadata_payload", {})
        submission.tcga_project_id = body.get("tcga_project_id", "") if input_data_type == "tcga" else ""
        if "quant_level" not in submission.metadata_payload:
            submission.metadata_payload["quant_level"] = quant_level
        submission.save()

        # Determine pipeline steps
        if input_data_type == "fastq":
            if assay_type == "small_rna":
                steps = ["fastqc", "trimmomatic", "bowtie_mirna", "mirna_quantify", "multiqc", "deseq2"]
            elif assay_type == "chip_seq":
                steps = ["fastqc", "trimmomatic", "bwa_align", "macs2_peaks", "featurecounts", "multiqc", "deseq2"]
            elif assay_type == "methylation":
                steps = ["fastqc", "trimmomatic", "bismark_prep", "bismark_align", "bismark_extract", "multiqc", "diff_methyl"]
            else:
                steps = ["fastqc", "trimmomatic", "hisat2", "featurecounts", "multiqc", "deseq2"]
            if reference_genome == "custom" and assay_type == "standard_rna":
                steps.insert(0, "hisat2_build")
        elif input_data_type == "alignment":
            steps = ["featurecounts", "deseq2"]
        elif input_data_type == "tcga":
            steps = ["tcga_download", "deseq2"]
        else:
            steps = ["deseq2"]

        job = AnalysisJob.objects.create(
            session=session_obj,
            parent_submission=submission,
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
            job.refresh_from_db()
            if job.status not in (AnalysisJob.Status.SUCCESS, AnalysisJob.Status.FAILED):
                job.status = AnalysisJob.Status.FAILED
                job.result_payload = {"error": "Task queue unavailable. Is the Celery worker running?"}
                job.save(update_fields=["status", "result_payload"])

        resp = {
            "job_id": str(job.job_id),
            "status": job.status,
        }
        if warnings:
            resp["warnings"] = warnings

        return JsonResponse(resp)


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

        # Detect stale RUNNING jobs
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
    """Return assets for the current session, optionally filtered by role."""

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
            try:
                job = AnalysisJob.objects.get(job_id=job_id, session=session_obj)
            except (AnalysisJob.DoesNotExist, ValueError):
                return JsonResponse({"error": "Job not found."}, status=404)
            qs = qs.filter(submission__in=session_obj.submissions.all())

        asset = qs.first()
        if asset and role:
            from django.urls import reverse
            return redirect(reverse("file_download", args=[asset.id]))

        assets = list(qs.values("id", "file_role", "local_path", "is_user_uploaded"))
        return JsonResponse({"assets": assets})


class FileDownloadView(View):
    """Serve a FileAsset for download, restricted to the owning session."""

    http_method_names = ["get"]

    def get(self, request, asset_id):
        session_obj = request.session_obj

        try:
            asset = FileAsset.objects.get(id=asset_id, session=session_obj)
        except (FileAsset.DoesNotExist, ValueError):
            raise Http404("File not found.")

        file_path = asset.local_path

        # Prevent path traversal
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


class ModuleRunView(View):
    """Trigger a Tier 2 analytical module for a completed Stage 2 session.

    Endpoint: POST /api/modules/<name>/run
    Validates the module name, confirms Stage 2 completed, and dispatches
    the corresponding Celery task.
    """

    http_method_names = ["post"]

    APPROVED_MODULES = {
        "SPLICING",       # A — IsoformSwitchAnalyzeR
        "RNA_EDITING",    # B — REDItools2
        "TIME_SERIES",    # C — ImpulseDE2
        "WGCNA",          # D — PyWGCNA
        "PATHWAY",        # E — gseapy
        "NETWORKS",       # F — arboreto / GRNBoost2 + STRING-DB
        "LIT_MINING",     # G — INDRA Bio API
        "SURVIVAL",       # H — lifelines
        "TCGA",           # I — TCGAbiolinks
        "BIOMARKER",      # J — MarkerDB API
        "MOFA",           # K — mofapy2
        "DIABLO",         # L — mixOmics DIABLO
    }

    def post(self, request, submission_id, module_name):
        session_obj = request.session_obj

        # ── Validate module name ──
        name_upper = module_name.upper()
        if name_upper not in self.APPROVED_MODULES:
            return JsonResponse(
                {"error": f"Unknown module '{module_name}'. "
                          f"Approved modules: {', '.join(sorted(self.APPROVED_MODULES))}"},
                status=400,
            )

        body = json.loads(request.body) if request.body else {}
        job_id = body.get("job_id")

        if not job_id:
            return JsonResponse({"error": "Missing job_id."}, status=400)

        # ── Verify Stage 2 completed successfully ──
        try:
            core_job = AnalysisJob.objects.get(
                job_id=job_id,
                session=session_obj,
                module_name="CORE_PIPELINE",
            )
        except (AnalysisJob.DoesNotExist, ValueError):
            return JsonResponse(
                {"error": "No core pipeline job found for this session."},
                status=404,
            )

        if core_job.status != AnalysisJob.Status.SUCCESS:
            return JsonResponse(
                {"error": "Stage 2 has not completed successfully. "
                          f"Current status: {core_job.status}"},
                status=409,
            )

        # ── Resolve parent submission ──
        try:
            submission = AnalysisSubmission.objects.get(
                submission_id=submission_id, session=session_obj
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse(
                {"error": "Submission not found."}, status=404
            )

        # ── Create the Tier 2 AnalysisJob ──
        module_job = AnalysisJob.objects.create(
            session=session_obj,
            parent_submission=submission,
            is_core_pipeline=False,
            module_name=name_upper,
            step_progress={
                "pipeline_steps": [name_upper],
                "completed_steps": [],
                "current_step": None,
                "failed_step": None,
            },
        )

        # ── Dispatch Celery task ──
        params = {k: v for k, v in body.items() if k != "job_id"}

        try:
            run_tier2_module.apply_async(
                args=[
                    str(session_obj.session_id),
                    str(core_job.job_id),
                    name_upper,
                ],
                kwargs={"params": params},
                task_id=str(module_job.job_id),
            )
        except Exception:
            module_job.status = AnalysisJob.Status.FAILED
            module_job.result_payload = {
                "error": "Task queue unavailable. Is the Celery worker running?"
            }
            module_job.save(update_fields=["status", "result_payload"])

        return JsonResponse({
            "job_id": str(module_job.job_id),
            "module": name_upper,
            "status": module_job.status,
        })


class TCGACohortsView(View):
    """Return the list of available TCGA disease cohorts."""

    http_method_names = ["get"]

    TCGA_PROJECTS = [
        {"id": "TCGA-ACC", "name": "Adrenocortical Carcinoma"},
        {"id": "TCGA-BLCA", "name": "Bladder Urothelial Carcinoma"},
        {"id": "TCGA-BRCA", "name": "Breast Invasive Carcinoma"},
        {"id": "TCGA-CESC", "name": "Cervical Squamous Cell Carcinoma"},
        {"id": "TCGA-CHOL", "name": "Cholangiocarcinoma"},
        {"id": "TCGA-COAD", "name": "Colon Adenocarcinoma"},
        {"id": "TCGA-DLBC", "name": "Diffuse Large B-cell Lymphoma"},
        {"id": "TCGA-ESCA", "name": "Esophageal Carcinoma"},
        {"id": "TCGA-GBM", "name": "Glioblastoma Multiforme"},
        {"id": "TCGA-HNSC", "name": "Head and Neck Squamous Cell Carcinoma"},
        {"id": "TCGA-KICH", "name": "Kidney Chromophobe"},
        {"id": "TCGA-KIRC", "name": "Kidney Renal Clear Cell Carcinoma"},
        {"id": "TCGA-KIRP", "name": "Kidney Renal Papillary Cell Carcinoma"},
        {"id": "TCGA-LAML", "name": "Acute Myeloid Leukemia"},
        {"id": "TCGA-LGG", "name": "Brain Lower Grade Glioma"},
        {"id": "TCGA-LIHC", "name": "Liver Hepatocellular Carcinoma"},
        {"id": "TCGA-LUAD", "name": "Lung Adenocarcinoma"},
        {"id": "TCGA-LUSC", "name": "Lung Squamous Cell Carcinoma"},
        {"id": "TCGA-MESO", "name": "Mesothelioma"},
        {"id": "TCGA-OV", "name": "Ovarian Serous Cystadenocarcinoma"},
        {"id": "TCGA-PAAD", "name": "Pancreatic Adenocarcinoma"},
        {"id": "TCGA-PCPG", "name": "Pheochromocytoma and Paraganglioma"},
        {"id": "TCGA-PRAD", "name": "Prostate Adenocarcinoma"},
        {"id": "TCGA-READ", "name": "Rectum Adenocarcinoma"},
        {"id": "TCGA-SARC", "name": "Sarcoma"},
        {"id": "TCGA-SKCM", "name": "Skin Cutaneous Melanoma"},
        {"id": "TCGA-STAD", "name": "Stomach Adenocarcinoma"},
        {"id": "TCGA-TGCT", "name": "Testicular Germ Cell Tumors"},
        {"id": "TCGA-THCA", "name": "Thyroid Carcinoma"},
        {"id": "TCGA-THYM", "name": "Thymoma"},
        {"id": "TCGA-UCEC", "name": "Uterine Corpus Endometrial Carcinoma"},
        {"id": "TCGA-UCS", "name": "Uterine Carcinosarcoma"},
        {"id": "TCGA-UVM", "name": "Uveal Melanoma"},
    ]

    VALID_IDS = {p["id"] for p in TCGA_PROJECTS}

    def get(self, request):
        return JsonResponse({"cohorts": self.TCGA_PROJECTS})


logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name="dispatch")
class TusdHookView(View):
    """Handle tusd HTTP hook callbacks.

    tusd sends a POST for each lifecycle event (pre-create, post-create,
    post-finish, etc.).  We only act on ``post-finish``: when the entire
    file has been written to disk, we register a :class:`FileAsset` so
    the rest of RNAseek can see it.

    The client must set these Tus ``Upload-Metadata`` keys:
        ``submission_id`` – UUID of the AnalysisSubmission
        ``file_role``     – a FileAsset.FileRole value (default: RAW_FASTQ)
        ``filename``      – original file name

    Session ownership is resolved via the ``Cookie`` header forwarded by
    tusd (``-hooks-http-forward-headers=Cookie``).
    """

    http_method_names = ["post"]

    def post(self, request):
        # ── Parse payload ──
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse(
                {"error": "Invalid JSON payload."},
                status=400,
                content_type="application/json",
            )

        # tusd v1 sends event type as Hook-Name header;
        # tusd v2 sends it as "Type" in the JSON body.
        hook_name = request.headers.get("Hook-Name", "") or payload.get("Type", "")

        # Acknowledge non-post-finish hooks immediately.
        if hook_name != "post-finish":
            return JsonResponse({}, content_type="application/json")

        try:
            upload = payload["Event"]["Upload"]
            tus_id = upload["ID"]
            meta = upload.get("MetaData") or {}
            storage = upload.get("Storage") or {}
        except (KeyError, TypeError) as exc:
            logger.warning("tusd hook: malformed payload – %s", exc)
            return JsonResponse(
                {"error": f"Missing required field: {exc}"},
                status=400,
                content_type="application/json",
            )

        # ── Extract metadata ──
        filename = meta.get("filename", "")
        submission_id = meta.get("submission_id", "")
        file_role = meta.get("file_role", FileAsset.FileRole.RAW_FASTQ)

        if not filename or not submission_id:
            return JsonResponse(
                {"error": "Upload-Metadata must include 'filename' and 'submission_id'."},
                status=400,
                content_type="application/json",
            )

        # ── Resolve session (attached by AnonymousSessionMiddleware) ──
        session_obj = getattr(request, "session_obj", None)
        if session_obj is None:
            logger.warning("tusd hook: no session resolved for upload %s", tus_id)
            return JsonResponse(
                {"error": "Session could not be resolved."},
                status=400,
                content_type="application/json",
            )

        # ── Validate submission belongs to this session ──
        try:
            submission = AnalysisSubmission.objects.get(
                submission_id=submission_id, session=session_obj
            )
        except (AnalysisSubmission.DoesNotExist, ValueError):
            return JsonResponse(
                {"error": "Invalid submission_id or session mismatch."},
                status=400,
                content_type="application/json",
            )

        # ── Validate file_role ──
        valid_roles = {choice[0] for choice in FileAsset.FileRole.choices}
        if file_role not in valid_roles:
            file_role = FileAsset.FileRole.RAW_FASTQ

        # ── Determine file path ──
        # tusd filestore writes to: <upload-dir>/<tus-id>
        # Storage.Path gives the absolute path if available.
        file_path = storage.get("Path", "")
        if not file_path:
            upload_dir = os.environ.get("MEDIA_ROOT", "/app/media")
            file_path = os.path.join(upload_dir, "uploads", tus_id)

        # Verify the file actually exists on disk.
        if not os.path.isfile(file_path):
            logger.error("tusd hook: file not found at %s for upload %s", file_path, tus_id)
            return JsonResponse(
                {"error": "Uploaded file not found on disk."},
                status=500,
                content_type="application/json",
            )

        # ── Move file to the submission directory ──
        safe_name = os.path.basename(filename)
        subdir = _subdir_for_role(file_role)
        dest_dir = os.path.join(submission.upload_dir, subdir)
        dest_path = os.path.join(dest_dir, safe_name)
        os.makedirs(dest_dir, exist_ok=True)

        try:
            shutil.move(file_path, dest_path)
        except OSError:
            # Cross-filesystem fallback (e.g. NFS): copy + remove
            shutil.copy2(file_path, dest_path)
            os.remove(file_path)

        # Clean up the .info sidecar file that tusd creates.
        info_path = file_path + ".info"
        if os.path.isfile(info_path):
            os.remove(info_path)

        # ── Register FileAsset ──
        asset = FileAsset.objects.create(
            session=session_obj,
            submission=submission,
            file_role=file_role,
            local_path=dest_path,
            is_user_uploaded=True,
        )

        logger.info(
            "tusd hook: registered FileAsset %s (%s) for submission %s",
            asset.id, safe_name, submission_id,
        )

        return JsonResponse(
            {"asset_id": str(asset.id)},
            content_type="application/json",
        )
