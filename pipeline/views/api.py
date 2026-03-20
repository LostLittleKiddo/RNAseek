"""API views — REST endpoints for uploads, pipeline triggers, and data retrieval."""

import json
import mimetypes
import os
import re as re_mod
import shutil

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt

from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset
from pipeline.tasks import run_core_pipeline
from pipeline.tasks.core import run_tier2_module


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
    """Receive a 5 MB chunk of a file upload and append it to a temp file.

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
        asset_id = None
        if is_last:
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
            "complete": is_last,
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
    VALID_INPUT_DATA_TYPES = {"fastq", "alignment", "matrix"}
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

        # ── Determine entry point ──
        input_data_type = body.get("input_data_type", "fastq")
        if input_data_type not in self.VALID_INPUT_DATA_TYPES:
            return JsonResponse({"error": "Invalid input_data_type."}, status=400)

        # ── Assay type (only relevant for FASTQ entry) ──
        assay_type = body.get("assay_type", "standard_rna")
        if input_data_type == "fastq" and assay_type not in self.VALID_ASSAY_TYPES:
            return JsonResponse({"error": "Invalid assay_type."}, status=400)

        # ── Fields only required for FASTQ entry ──
        library_type = body.get("library_type", "")
        strandedness = body.get("strandedness", "unstranded")

        if input_data_type == "fastq":
            if library_type not in self.VALID_LIBRARY_TYPES:
                return JsonResponse({"error": "Invalid library_type."}, status=400)
            if strandedness not in self.VALID_STRANDEDNESS:
                return JsonResponse({"error": "Invalid strandedness."}, status=400)

        # ── Reference genome ──
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

        # ── Validate input files ──
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

        # ── Custom genome files ──
        if input_data_type in ("fastq", "alignment") and reference_genome == "custom":
            custom_name = body.get("custom_genome_name", "").strip()
            if not custom_name:
                return JsonResponse({"error": "Custom genome name is required."}, status=400)

            if input_data_type == "fastq":
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
                has_annotation = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
                ).exists()
                if not has_annotation:
                    return JsonResponse(
                        {"error": "Custom genome requires a GTF/GFF annotation file."},
                        status=400,
                    )

        # ── Metadata validation ──
        meta = body.get("metadata_payload", {})

        samples = meta.get("samples", [])
        if not samples:
            return JsonResponse(
                {"error": "Metadata requires sample data."},
                status=400,
            )

        if metadata_mode == "upload" and samples and isinstance(samples[0], dict):
            first_col = list(samples[0].keys())[0] if samples[0] else ""
            if first_col.strip().lower() != "sample":
                return JsonResponse(
                    {"error": "The first column of metadata must be named 'sample'."},
                    status=400,
                )

        # ── Metadata sample-name matching ──
        if metadata_mode == "upload" and samples and isinstance(samples[0], dict):
            sample_col = list(samples[0].keys())[0]
            meta_sample_ids = {
                (row.get(sample_col) or "").strip() for row in samples
            }
            meta_sample_ids.discard("")

            expected_stems = set()
            if input_data_type == "fastq":
                fastq_assets = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.RAW_FASTQ
                )
                fq_names = [
                    os.path.basename(p)
                    for p in fastq_assets.values_list("local_path", flat=True)
                ]
                if library_type == "paired":
                    pair_re = re_mod.compile(
                        r'^(.+?)(?:_R[12]|_[12])\.(?:fq|fastq)\.gz$', re_mod.IGNORECASE
                    )
                    for name in fq_names:
                        m = pair_re.match(name)
                        if m:
                            expected_stems.add(m.group(1))
                else:
                    for name in fq_names:
                        stem = re_mod.sub(
                            r'\.(fq|fastq)(\.gz)?$', '', name, flags=re_mod.IGNORECASE
                        )
                        expected_stems.add(stem)
            elif input_data_type == "alignment":
                bam_assets = submission.file_assets.filter(
                    file_role=FileAsset.FileRole.ALIGNMENT_BAM
                )
                for p in bam_assets.values_list("local_path", flat=True):
                    stem = re_mod.sub(
                        r'\.(bam|cram)$', '', os.path.basename(p), flags=re_mod.IGNORECASE
                    )
                    expected_stems.add(stem)

            if expected_stems:
                unmatched = expected_stems - meta_sample_ids
                if unmatched:
                    return JsonResponse(
                        {"error": (
                            f"Metadata is missing rows for uploaded samples: "
                            f"{', '.join(sorted(unmatched))}. "
                            f"The 'sample' column must contain the filename stem "
                            f"(without extension)."
                        )},
                        status=400,
                    )

        # ── Column mapping ──
        col_mapping = meta.get("column_mapping", {})
        primary_group = col_mapping.get("primary_group")
        if not primary_group:
            return JsonResponse(
                {"error": "A primary group column must be selected."},
                status=400,
            )

        # Validate contrasts
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

        # ── Thresholds ──
        try:
            adj_pvalue = float(body.get("adjusted_pvalue", 0.05))
            min_log2fc = float(body.get("min_log2fc", -1.0))
            max_log2fc = float(body.get("max_log2fc", 1.0))
        except (ValueError, TypeError):
            return JsonResponse({"error": "Invalid threshold values."}, status=400)

        if not (0 < adj_pvalue <= 1):
            return JsonResponse(
                {"error": "adjusted_pvalue must be between 0 and 1."}, status=400
            )

        # ── Persist payload ──
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

    def post(self, request, module_name):
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

        # ── Create the Tier 2 AnalysisJob ──
        module_job = AnalysisJob.objects.create(
            session=session_obj,
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
