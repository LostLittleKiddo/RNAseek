import csv
import glob
import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import shared_task

logger = logging.getLogger(__name__)

# ── Parallelism settings ──
_CPU_COUNT = os.cpu_count() or 4
# Threads per individual tool invocation (HISAT2, samtools, etc.)
_TOOL_THREADS = max(4, _CPU_COUNT // 2)
# Max parallel samples to process simultaneously
_PARALLEL_SAMPLES = max(2, _CPU_COUNT // _TOOL_THREADS)

# ── Paths to pre-indexed reference genomes ──
_GENOME_BASE = os.path.join(os.path.dirname(__file__), "reference_genomes")

# Mapping: genome_key (from frontend) → folder name under reference_genomes/
_GENOME_FOLDER_MAP = {
    "hg38":      "Human_GRCh38",
    "mm39":      "Mouse_GRCm39",
    "mm10":      "Mouse_GRCm38",
    "rn7":       "Rat_rn7",
    "danRer11":  "Zebrafish_GRCz11",
    "galGal6":   "Chicken_GRCg6a",
    "susScr11":  "Pig_Sscrofa11.1",
    "dm6":       "Drosophila_dm6",
    "wbcel235":  "Celegans_WBcel235",
    "r64":       "Yeast_sacCer3",
    "araTha":    "Arabidopsis_TAIR10",
}


def _genome_paths(genome_key):
    """Return (hisat2_index_prefix, fasta_path, gtf_path) for a genome key.

    Auto-detects files inside the reference_genomes/<folder>/ directory:
      - HISAT2 index prefix: derived from *.1.ht2 files
      - GTF: first *.gtf file found
      - FASTA: first *.fa or *.fasta file found
    """
    folder_name = _GENOME_FOLDER_MAP.get(genome_key)
    if not folder_name:
        raise ValueError(f"Unknown genome key: {genome_key}")

    base = os.path.join(_GENOME_BASE, folder_name)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"Genome directory not found: {base}")

    # HISAT2 index prefix: find *.1.ht2 and strip the '.1.ht2' suffix
    ht2_files = glob.glob(os.path.join(base, "*.1.ht2"))
    hisat2_idx = ht2_files[0].replace(".1.ht2", "") if ht2_files else None

    # GTF annotation
    gtf_files = glob.glob(os.path.join(base, "*.gtf"))
    genome_gtf = gtf_files[0] if gtf_files else None

    # FASTA genome
    fasta_files = (glob.glob(os.path.join(base, "*.fa")) +
                   glob.glob(os.path.join(base, "*.fasta")))
    genome_fasta = fasta_files[0] if fasta_files else None

    return hisat2_idx, genome_fasta, genome_gtf


def _run(cmd, cwd=None):
    """Execute a shell command, raising on failure."""
    logger.info("Running: %s", cmd)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("STDERR: %s", result.stderr)
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd}\n{result.stderr}")
    return result


def _strandedness_hisat2(strandedness, library_type):
    """Map strandedness + library type to HISAT2 --rna-strandness flag."""
    if strandedness == "unstranded":
        return ""
    if library_type == "paired":
        return "--rna-strandness RF" if strandedness == "fr-firststrand" else "--rna-strandness FR"
    return "--rna-strandness R" if strandedness == "fr-firststrand" else "--rna-strandness F"


def _strandedness_fc(strandedness):
    """Map strandedness to featureCounts -s flag (0/1/2)."""
    if strandedness == "fr-firststrand":
        return "2"
    if strandedness == "fr-secondstrand":
        return "1"
    return "0"


def _feature_type(quant_level):
    """Map quant_level to featureCounts -t flag."""
    return "transcript" if quant_level == "transcript" else "exon"


def _parse_metadata_csv(csv_path):
    """Parse the user-uploaded metadata CSV into a list of dicts."""
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _pair_fastqs(fastq_paths):
    """Group paired-end FASTQs by prefix. Returns [(r1, r2), ...]."""
    import re
    pairs = {}
    pattern = re.compile(r'^(.+?)(?:_R([12])|_([12]))\.(?:fq|fastq)\.gz$', re.IGNORECASE)
    for path in sorted(fastq_paths):
        name = os.path.basename(path)
        m = pattern.match(name)
        if m:
            prefix = m.group(1)
            read_num = m.group(2) or m.group(3)
            pairs.setdefault(prefix, {})[read_num] = path
    result = []
    for prefix in sorted(pairs):
        r1 = pairs[prefix].get("1")
        r2 = pairs[prefix].get("2")
        if r1 and r2:
            result.append((r1, r2))
    return result


def _update_step(job, step, completed=False):
    """Update step_progress on the job. Call with completed=False to mark
    a step as 'running', then completed=True when it finishes.
    Also emits the update to the WebSocket group for real-time UI updates."""
    job.refresh_from_db(fields=["step_progress"])
    progress = job.step_progress or {}
    completed_steps = progress.get("completed_steps", [])

    if completed:
        if step not in completed_steps:
            completed_steps.append(step)
        progress["completed_steps"] = completed_steps
        progress["current_step"] = None
    else:
        progress["current_step"] = step
        progress["completed_steps"] = completed_steps

    job.step_progress = progress
    job.save(update_fields=["step_progress"])

    _emit_progress(job)


def _emit_progress(job):
    """Send the current job progress to the WebSocket group.

    Uses the synchronous channel layer API so it works inside Celery workers.
    Silently no-ops if the channel layer is unavailable.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        group_name = f"pipeline_{job.job_id}"
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "pipeline.progress",
                "data": {
                    "job_id": str(job.job_id),
                    "status": job.status,
                    "step_progress": job.step_progress,
                },
            },
        )
    except Exception:
        # Non-critical: log but don't disrupt the pipeline
        logger.debug("Failed to emit WebSocket progress for job %s", job.job_id, exc_info=True)


def _register_stage2_assets(submission, stats_result, qc_dir=None):
    """Register Stage 2 output files (normalized counts, DEG tables, MultiQC) as FileAssets."""
    from pipeline.models import FileAsset

    # Normalized counts
    norm_path = stats_result.get("normalized_counts")
    if norm_path and os.path.isfile(norm_path):
        FileAsset.objects.create(
            session_id=submission.session_id,
            submission=submission,
            file_role=FileAsset.FileRole.NORMALIZED_COUNTS,
            local_path=norm_path,
            is_user_uploaded=False,
        )

    # DEG result tables
    for deg_path in stats_result.get("deg_results", []):
        if os.path.isfile(deg_path):
            FileAsset.objects.create(
                session_id=submission.session_id,
                submission=submission,
                file_role=FileAsset.FileRole.DEG_TABLE,
                local_path=deg_path,
                is_user_uploaded=False,
            )

    # MultiQC HTML report
    if qc_dir:
        mqc_html = os.path.join(qc_dir, "multiqc_report.html")
        if os.path.isfile(mqc_html):
            FileAsset.objects.create(
                session_id=submission.session_id,
                submission=submission,
                file_role=FileAsset.FileRole.MULTIQC_REPORT,
                local_path=mqc_html,
                is_user_uploaded=False,
            )


@shared_task(bind=True)
def run_core_pipeline(self, session_id, submission_id):
    """Core Pipeline router: dispatches to the correct entry point.

    Routes:
      - fastq:     Full pipeline (FastQC -> Trim -> HISAT2 -> featureCounts -> Stage 2)
      - alignment:  Skip to featureCounts on uploaded BAMs -> Stage 2
      - matrix:    Skip to Stage 2 stats with user-provided count matrix
    """
    from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset

    job = AnalysisJob.objects.get(job_id=self.request.id)
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        submission = AnalysisSubmission.objects.get(submission_id=submission_id)
        input_type = submission.input_data_type

        if input_type == "fastq":
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


def _route_fastq(submission, job):
    """Route A: Full pipeline from FASTQ files."""
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    raw_dir = os.path.join(work_dir, "raw")
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (trimmed_dir, aligned_dir, counts_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    fastq_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        ).values_list("local_path", flat=True)
    )

    library_type = submission.library_type
    strandedness = submission.strandedness
    quant_level = submission.metadata_payload.get("quant_level", "gene")

    # Resolve genome paths (pre-built index used; custom genomes built later)
    genome_key = submission.reference_genome
    hisat2_idx, genome_fasta, genome_gtf = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )

    # --- Step 0 (custom only): Build HISAT2 index from user FASTA ---
    if genome_key == "custom":
        _update_step(job, "hisat2_build")
        if not genome_fasta or not os.path.isfile(genome_fasta):
            raise RuntimeError(
                f"Custom genome FASTA file not found. "
                f"Expected path: {genome_fasta or 'N/A'}"
            )
        idx_prefix = os.path.join(work_dir, "custom_genome", "hisat2_index")
        _run(f"hisat2-build -p {_CPU_COUNT} {genome_fasta} {idx_prefix}")
        hisat2_idx = idx_prefix
        _update_step(job, "hisat2_build", completed=True)

    # --- Step 1: FastQC on raw FASTQs (all samples, multi-threaded) ---
    _update_step(job, "fastqc")
    _run(f"fastqc -o {qc_dir} -t {_CPU_COUNT} {' '.join(fastq_assets)}")
    _update_step(job, "fastqc", completed=True)

    # --- Step 2: Trimmomatic (parallel per sample) ---
    _update_step(job, "trimmomatic")
    trimmed_files = []
    if library_type == "paired":
        pairs = _pair_fastqs(fastq_assets)

        def _trim_paired(r1, r2):
            prefix = os.path.basename(r1).split("_R1")[0].split("_1.")[0]
            out_r1 = os.path.join(trimmed_dir, f"{prefix}_R1_trimmed.fq.gz")
            out_r1_unpaired = os.path.join(trimmed_dir, f"{prefix}_R1_unpaired.fq.gz")
            out_r2 = os.path.join(trimmed_dir, f"{prefix}_R2_trimmed.fq.gz")
            out_r2_unpaired = os.path.join(trimmed_dir, f"{prefix}_R2_unpaired.fq.gz")
            _run(
                f"trimmomatic PE -threads {_TOOL_THREADS} "
                f"{r1} {r2} "
                f"{out_r1} {out_r1_unpaired} "
                f"{out_r2} {out_r2_unpaired} "
                f"ILLUMINACLIP:TruSeq3-PE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
            )
            return (out_r1, out_r2, prefix)

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_trim_paired, r1, r2) for r1, r2 in pairs]
            for fut in as_completed(futures):
                trimmed_files.append(fut.result())
    else:
        def _trim_single(fq):
            name = os.path.basename(fq)
            stem = name.replace(".fq.gz", "").replace(".fastq.gz", "")
            out = os.path.join(trimmed_dir, f"{stem}_trimmed.fq.gz")
            _run(
                f"trimmomatic SE -threads {_TOOL_THREADS} "
                f"{fq} {out} "
                f"ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
            )
            return (out, stem)

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_trim_single, fq) for fq in fastq_assets]
            for fut in as_completed(futures):
                trimmed_files.append(fut.result())
    _update_step(job, "trimmomatic", completed=True)

    # --- Step 3: HISAT2 -> samtools BAM (parallel per sample) ---
    _update_step(job, "hisat2")
    strand_flag = _strandedness_hisat2(strandedness, library_type)
    bam_files = []
    _sam_threads = max(2, _TOOL_THREADS // 2)

    if library_type == "paired":
        def _align_paired(r1, r2, prefix):
            bam_path = os.path.join(aligned_dir, f"{prefix}.bam")
            _run(
                f"hisat2 -p {_TOOL_THREADS} --dta {strand_flag} "
                f"-x {hisat2_idx} -1 {r1} -2 {r2} "
                f"| samtools view -b -@ {_sam_threads} "
                f"| samtools sort -@ {_sam_threads} -o {bam_path}"
            )
            _run(f"samtools index -@ {_sam_threads} {bam_path}")
            return bam_path

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_align_paired, r1, r2, pf) for r1, r2, pf in trimmed_files]
            for fut in as_completed(futures):
                bam_files.append(fut.result())
    else:
        def _align_single(fq, stem):
            bam_path = os.path.join(aligned_dir, f"{stem}.bam")
            _run(
                f"hisat2 -p {_TOOL_THREADS} --dta {strand_flag} "
                f"-x {hisat2_idx} -U {fq} "
                f"| samtools view -b -@ {_sam_threads} "
                f"| samtools sort -@ {_sam_threads} -o {bam_path}"
            )
            _run(f"samtools index -@ {_sam_threads} {bam_path}")
            return bam_path

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_align_single, fq, stem) for fq, stem in trimmed_files]
            for fut in as_completed(futures):
                bam_files.append(fut.result())
    _update_step(job, "hisat2", completed=True)

    # --- Step 4: featureCounts ---
    _update_step(job, "featurecounts")
    count_matrix_path = _run_featurecounts(
        bam_files, genome_gtf, strandedness, quant_level, library_type, work_dir
    )
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )
    _update_step(job, "featurecounts", completed=True)

    # --- Step 5: MultiQC ---
    _update_step(job, "multiqc")
    _run(f"multiqc {work_dir} -o {qc_dir} --force --no-data-dir")
    _update_step(job, "multiqc", completed=True)

    # --- Stage 2: Statistical analysis ---
    _update_step(job, "deseq2")
    stats_result = run_stage2_stats(submission)
    _update_step(job, "deseq2", completed=True)

    _register_stage2_assets(submission, stats_result, qc_dir=qc_dir)

    return {
        "count_matrix": count_matrix_path,
        "qc_dir": qc_dir,
        **stats_result,
    }


def _route_alignment(submission, job):
    """Route B: Start from uploaded BAM/CRAM files -> featureCounts -> Stage 2."""
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")
    os.makedirs(counts_dir, exist_ok=True)
    os.makedirs(qc_dir, exist_ok=True)

    bam_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.ALIGNMENT_BAM
        ).values_list("local_path", flat=True)
    )

    strandedness = submission.strandedness or "unstranded"
    quant_level = submission.metadata_payload.get("quant_level", "gene")
    library_type = submission.library_type or "single"

    # Resolve genome (only GTF needed for featureCounts)
    genome_key = submission.reference_genome
    _, _, genome_gtf = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )

    # Convert CRAM to BAM if needed (featureCounts requires BAM) — parallel
    bam_files = []
    aligned_dir = os.path.join(work_dir, "aligned")
    os.makedirs(aligned_dir, exist_ok=True)
    _sam_threads = max(2, _TOOL_THREADS // 2)

    def _convert_or_index(path):
        if path.endswith(".cram"):
            bam_path = os.path.join(
                aligned_dir, os.path.basename(path).replace(".cram", ".bam")
            )
            _run(f"samtools view -b -@ {_sam_threads} -o {bam_path} {path}")
            _run(f"samtools index -@ {_sam_threads} {bam_path}")
            return bam_path
        else:
            if not os.path.exists(path + ".bai"):
                _run(f"samtools index -@ {_sam_threads} {path}")
            return path

    with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
        bam_files = list(pool.map(_convert_or_index, bam_assets))

    # featureCounts
    _update_step(job, "featurecounts")
    count_matrix_path = _run_featurecounts(
        bam_files, genome_gtf, strandedness, quant_level, library_type, work_dir
    )
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )
    _update_step(job, "featurecounts", completed=True)

    # Stage 2
    _update_step(job, "deseq2")
    stats_result = run_stage2_stats(submission)
    _update_step(job, "deseq2", completed=True)

    _register_stage2_assets(submission, stats_result, qc_dir=qc_dir)

    return {"count_matrix": count_matrix_path, "qc_dir": qc_dir, **stats_result}


def _route_matrix(submission, job):
    """Route C: Start from user-provided count matrix → Stage 2 stats only."""
    import pandas as pd

    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    os.makedirs(counts_dir, exist_ok=True)

    # Find the uploaded count matrix
    matrix_asset = submission.file_assets.filter(
        file_role=FileAsset.FileRole.USER_COUNT_MATRIX
    ).first()
    if not matrix_asset:
        raise RuntimeError("No count matrix file found.")

    user_path = matrix_asset.local_path

    # Detect separator and load
    sep = "\t" if user_path.endswith(".tsv") else ","
    df = pd.read_csv(user_path, sep=sep, index_col=0)

    # Validate: all values should be non-negative integers (raw counts)
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise ValueError("Count matrix is empty.")
    if not all(df.dtypes.apply(lambda dt: pd.api.types.is_numeric_dtype(dt))):
        raise ValueError(
            "Count matrix contains non-numeric columns. "
            "Ensure all sample columns contain raw integer counts."
        )
    if (df < 0).any().any():
        raise ValueError("Count matrix contains negative values.")

    # Copy to canonical location for Stage 2
    canonical_path = os.path.join(counts_dir, "raw_counts.csv")
    df.to_csv(canonical_path)

    # Register as pipeline-generated count matrix too
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=canonical_path,
        is_user_uploaded=False,
    )

    # Stage 2
    _update_step(job, "deseq2")
    stats_result = run_stage2_stats(submission)
    _update_step(job, "deseq2", completed=True)

    _register_stage2_assets(submission, stats_result)

    return {"count_matrix": canonical_path, **stats_result}


def _resolve_genome(genome_key, work_dir, submission=None, **kwargs):
    """Resolve genome paths. Returns (hisat2_idx, fasta, gtf).

    For pre-built genomes: uses reference_genomes/ with pre-built HISAT2 index.
    For custom genomes: queries FileAsset records first, falls back to glob.
    """
    if genome_key == "custom":
        from pipeline.models import FileAsset

        genome_fasta = None
        genome_gtf = None

        # Primary: query the FileAsset model for the recorded local_path
        if submission is not None:
            fasta_asset = submission.file_assets.filter(
                file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
            ).first()
            if fasta_asset and os.path.isfile(fasta_asset.local_path):
                genome_fasta = fasta_asset.local_path

            gtf_asset = submission.file_assets.filter(
                file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
            ).first()
            if gtf_asset and os.path.isfile(gtf_asset.local_path):
                genome_gtf = gtf_asset.local_path

        # Fallback: glob the custom_genome directory
        if not genome_fasta or not genome_gtf:
            custom_dir = os.path.join(work_dir, "custom_genome")
            if not genome_fasta:
                fasta_files = (
                    glob.glob(os.path.join(custom_dir, "*.fa"))
                    + glob.glob(os.path.join(custom_dir, "*.fasta"))
                    + glob.glob(os.path.join(custom_dir, "*.fna"))
                )
                genome_fasta = fasta_files[0] if fasta_files else None
            if not genome_gtf:
                gtf_files = (
                    glob.glob(os.path.join(custom_dir, "*.gtf"))
                    + glob.glob(os.path.join(custom_dir, "*.gff"))
                    + glob.glob(os.path.join(custom_dir, "*.gff3"))
                )
                genome_gtf = gtf_files[0] if gtf_files else None

        # Index building is handled as a tracked step in _route_fastq()
        hisat2_idx = None
        return hisat2_idx, genome_fasta, genome_gtf
    else:
        # Pre-built genome — no index building needed
        return _genome_paths(genome_key)


def _run_featurecounts(bam_files, genome_gtf, strandedness, quant_level,
                       library_type, work_dir):
    """Run featureCounts and convert output to clean CSV. Returns CSV path."""
    counts_dir = os.path.join(work_dir, "counts")
    os.makedirs(counts_dir, exist_ok=True)
    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")
    fc_output = os.path.join(counts_dir, "featurecounts_output.txt")
    s_flag = _strandedness_fc(strandedness)
    t_flag = _feature_type(quant_level)
    paired_flag = "-p --countReadPairs" if library_type == "paired" else ""

    # Detect annotation format: GFF/GFF3 files need -F GFF
    annotation_ext = os.path.splitext(genome_gtf)[1].lower() if genome_gtf else ".gtf"
    if annotation_ext in (".gff", ".gff3"):
        format_flag = "-F GFF"
        # GFF3 uses 'gene_id' if present, otherwise 'ID' for gene features
        g_attr = _detect_gff_gene_attr(genome_gtf)
    else:
        format_flag = ""
        g_attr = "gene_id"

    _run(
        f"featureCounts {paired_flag} "
        f"-T {_CPU_COUNT} -s {s_flag} -t {t_flag} -g {g_attr} "
        f"{format_flag} "
        f"-a {genome_gtf} "
        f"-o {fc_output} "
        f"{' '.join(bam_files)}"
    )

    _featurecounts_to_csv(fc_output, count_matrix_path)
    return count_matrix_path


def _detect_gff_gene_attr(gff_path):
    """Peek at a GFF/GFF3 file to determine the gene ID attribute name.

    Checks the first 200 feature lines for 'gene_id' in the attributes column.
    Falls back to 'ID' (standard GFF3) if 'gene_id' is not found.
    """
    checked = 0
    with open(gff_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            if checked >= 200:
                break
            checked += 1
            attrs = line.rstrip().split("\t")[-1] if "\t" in line else ""
            if "gene_id" in attrs:
                return "gene_id"
    return "ID"


def _featurecounts_to_csv(fc_path, csv_path):
    """Convert featureCounts tab-delimited output to a clean CSV.

    featureCounts output has a comment header (starting with #),
    followed by a header line: Geneid, Chr, Start, End, Strand, Length, then sample columns.
    We extract Geneid + sample counts only.
    """
    with open(fc_path) as fin:
        lines = [line for line in fin if not line.startswith("#")]

    if not lines:
        raise RuntimeError("featureCounts output is empty.")

    header = lines[0].strip().split("\t")
    # Columns 0=Geneid, 1-5=Chr/Start/End/Strand/Length, 6+=samples
    sample_cols = header[6:]
    # Clean sample column names (featureCounts uses full paths)
    clean_names = [os.path.basename(c).replace(".bam", "").replace(".cram", "") for c in sample_cols]

    with open(csv_path, "w", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["gene_id"] + clean_names)
        for line in lines[1:]:
            fields = line.strip().split("\t")
            writer.writerow([fields[0]] + fields[6:])
