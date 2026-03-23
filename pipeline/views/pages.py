"""Page views — Django TemplateViews for the frontend."""

import json

from django.shortcuts import get_object_or_404
from django.views.generic import TemplateView

from pipeline.models import AnalysisJob, FileAsset


class HomeView(TemplateView):
    template_name = "pipeline/home.html"


class TutorialsView(TemplateView):
    template_name = "pipeline/tutorials.html"


class WorkspacesView(TemplateView):
    template_name = "pipeline/workspaces.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings

        ctx = super().get_context_data(**kwargs)
        session_obj = self.request.session_obj
        ctx["is_production"] = settings.IS_PRODUCTION
        ctx["jobs"] = list(
            session_obj.analysis_jobs.filter(
                is_core_pipeline=True,
            ).order_by("-created_at").values(
                "job_id",
                "module_name",
                "status",
                "created_at",
                "parent_submission__submission_name",
            )
        )
        return ctx


class NewSubmissionView(TemplateView):
    template_name = "pipeline/new_submission.html"
    nav_step = 1

    def get_context_data(self, **kwargs):
        from django.conf import settings

        ctx = super().get_context_data(**kwargs)
        ctx["nav_step"] = self.nav_step
        ctx["is_production"] = settings.IS_PRODUCTION
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
        # Standard RNA (Track A)
        {"key": "hisat2_build", "title": "Build HISAT2 Index", "desc": "Building genome index from custom FASTA (may take a while)"},
        {"key": "fastqc", "title": "FastQC", "desc": "Quality control on raw reads"},
        {"key": "trimmomatic", "title": "Trimmomatic", "desc": "Adapter trimming & quality filtering"},
        {"key": "hisat2", "title": "HISAT2 Alignment", "desc": "Splice-aware alignment to reference genome"},
        {"key": "featurecounts", "title": "featureCounts", "desc": "Gene-level read quantification"},
        {"key": "multiqc", "title": "MultiQC Report", "desc": "Aggregate all QC logs into a single report"},
        {"key": "deseq2", "title": "Batch Correction & DESeq2", "desc": "Combat-seq normalization & differential expression testing"},
        # Small RNA / miRNA (Track B)
        {"key": "bowtie_mirna", "title": "Bowtie miRNA Alignment", "desc": "Aligning ultra-short reads against miRBase database"},
        {"key": "mirna_quantify", "title": "miRNA Quantification", "desc": "Per-miRNA read counting via samtools idxstats"},
        # ChIP-seq (Track C)
        {"key": "bwa_align", "title": "BWA Alignment", "desc": "Aligning reads to reference genome with BWA MEM"},
        {"key": "macs2_peaks", "title": "MACS2 Peak Calling", "desc": "Identifying transcription factor binding sites"},
        # DNA Methylation (Track C)
        {"key": "bismark_prep", "title": "Bismark Genome Prep", "desc": "Preparing bisulfite-converted genome index"},
        {"key": "bismark_align", "title": "Bismark Alignment", "desc": "Aligning bisulfite-converted reads"},
        {"key": "bismark_extract", "title": "Methylation Extraction", "desc": "Decoding C-to-T mutations into methylation beta-values"},
        {"key": "diff_methyl", "title": "Differential Methylation", "desc": "methylKit differential methylation analysis"},
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

        # Submission ID for module run API calls
        submission = job.parent_submission
        ctx["submission_id"] = str(submission.submission_id) if submission else ""

        # Module jobs: non-core-pipeline jobs linked to same submission
        module_jobs_map = {}
        if submission:
            for mj in AnalysisJob.objects.filter(
                parent_submission=submission,
                is_core_pipeline=False,
            ).values("module_name", "status", "result_payload", "updated_at"):
                module_jobs_map[mj["module_name"]] = {
                    "status": mj["status"],
                    "payload": mj["result_payload"] or {},
                    "updated_at": mj["updated_at"].isoformat() if mj["updated_at"] else None,
                }
        ctx["module_jobs_json"] = json.dumps(module_jobs_map)

        return ctx


