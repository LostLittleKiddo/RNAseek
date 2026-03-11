"""Non-FASTQ pipeline routes: alignment and count matrix entry points.

Also contains the shared Stage 2 asset registration helper.
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor

from pipeline.tasks._constants import _PARALLEL_SAMPLES, _TOOL_THREADS
from pipeline.tasks._featurecounts import _run_featurecounts
from pipeline.tasks._genome import _resolve_genome
from pipeline.tasks._helpers import _q, _run, _update_step

logger = logging.getLogger(__name__)


def _register_stage2_assets(submission, stats_result, qc_dir=None):
    """Register Stage 2 output files as FileAssets."""
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


def _route_alignment(submission, job):
    """Route B: Start from uploaded BAM/CRAM files → featureCounts → Stage 2."""
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

    genome_key = submission.reference_genome
    _, _, genome_gtf = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )

    # Convert CRAM to BAM if needed — parallel
    bam_files = []
    aligned_dir = os.path.join(work_dir, "aligned")
    os.makedirs(aligned_dir, exist_ok=True)
    _sam_threads = max(2, _TOOL_THREADS // 2)

    def _convert_or_index(path):
        if path.endswith(".cram"):
            bam_path = os.path.join(
                aligned_dir, os.path.basename(path).replace(".cram", ".bam")
            )
            _run(f"samtools view -b -@ {_sam_threads} -o {_q(bam_path)} {_q(path)}")
            _run(f"samtools index -@ {_sam_threads} {_q(bam_path)}")
            return bam_path
        else:
            if not os.path.exists(path + ".bai"):
                _run(f"samtools index -@ {_sam_threads} {_q(path)}")
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
    """Route C: Start from user-provided count matrix → Stage 2 stats only.

    Validates that the matrix contains raw integer counts (not TPM/FPKM/RPKM)
    to prevent false-positive DESeq2 results.
    """
    import pandas as pd

    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    os.makedirs(counts_dir, exist_ok=True)

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

    # FIX: Detect normalized data (TPM/FPKM/RPKM) which would produce
    # invalid DESeq2 results.  Raw counts are integers; normalized values
    # have many fractional components.
    total_values = df.size
    if total_values > 0:
        fractional_ratio = ((df % 1) != 0).sum().sum() / total_values
        if fractional_ratio > 0.3:
            raise ValueError(
                "Count matrix appears to contain normalized values (e.g., "
                "TPM, FPKM, or RPKM) rather than raw integer counts. "
                "DESeq2 requires raw, unnormalized count data. "
                f"{fractional_ratio:.0%} of values are non-integer."
            )

    # Round to integers for DESeq2 (handles minor floating-point noise)
    df = df.round(0).astype(int)

    # Copy to canonical location for Stage 2
    canonical_path = os.path.join(counts_dir, "raw_counts.csv")
    df.to_csv(canonical_path)

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
