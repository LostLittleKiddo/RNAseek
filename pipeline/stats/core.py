"""Stage 2: Dynamic DESeq2 with Flexible Experimental Designs — orchestrator.

Uses rpy2 to bridge Python <-> R for:
  1. Gene filtering (remove ultra-low counts)
  2. Conditional ComBat_seq batch correction (if batch_effect column mapped)
  3. Mahalanobis outlier detection on PCA
  4. DESeq2 differential expression with a dynamically-built design formula
  5. Multi-contrast results extraction for >2 group designs

The design formula is constructed from the column_mapping payload:
    ~ covariate1 + covariate2 + batch_effect + primary_group
Primary group is ALWAYS placed last so DESeq2 uses it for default contrasts.
"""

import json
import logging
import os

from pipeline.stats._helpers import (
    _align_samples,
    _combat_seq,
    _detect_outliers,
    _filter_low_counts,
    _load_metadata,
)
from pipeline.stats._deseq2 import _run_deseq2
from pipeline.stats._plots import _generate_plot_data

logger = logging.getLogger(__name__)


def run_stage2_stats(submission):
    """Run Stage 2 stats on a completed Stage 1 submission.

    Reads column_mapping and contrasts from submission.metadata_payload
    to construct a dynamic DESeq2 design and extract per-contrast results.

    Returns a dict of result paths and summary data for the job payload.
    """
    import pandas as pd

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    stats_dir = os.path.join(work_dir, "stats")
    os.makedirs(stats_dir, exist_ok=True)

    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")

    counts_df = pd.read_csv(count_matrix_path, index_col=0)

    metadata = _load_metadata(submission)
    if metadata is None:
        raise RuntimeError("Could not load metadata for statistical analysis.")

    payload = submission.metadata_payload or {}
    column_mapping = payload.get("column_mapping", {})
    contrasts_list = payload.get("contrasts", [])

    primary_group = column_mapping.get("primary_group")
    batch_effect = column_mapping.get("batch_effect")

    if not primary_group:
        raise RuntimeError(
            "No primary_group column specified in column_mapping. "
            "Cannot run differential expression without a grouping variable."
        )

    metadata, counts_df = _align_samples(metadata, counts_df)

    if len(counts_df.columns) < 2:
        raise RuntimeError(
            "Fewer than 2 samples after alignment. "
            "Check that metadata sample names match FASTQ file names."
        )

    # ── Step 1: Gene filtering ──
    counts_df = _filter_low_counts(counts_df, min_total=10)
    logger.info("After filtering: %d genes remain", len(counts_df))

    has_batch = (
        batch_effect
        and batch_effect in metadata.columns
        and metadata[batch_effect].notna().all()
        and metadata[batch_effect].nunique() > 1
    )

    # ── Step 2: Conditional batch correction ──
    if has_batch:
        logger.info("Batch column '%s' detected – running ComBat_seq", batch_effect)
        counts_df = _combat_seq(counts_df, metadata, batch_effect, primary_group)
        corrected_path = os.path.join(stats_dir, "batch_corrected_counts.csv")
        counts_df.to_csv(corrected_path)

    # ── Step 3: Outlier detection ──
    outlier_flags = _detect_outliers(counts_df)
    outlier_path = os.path.join(stats_dir, "outlier_flags.json")
    with open(outlier_path, "w") as f:
        json.dump(outlier_flags, f, indent=2)

    # ── Step 4: DESeq2 differential expression ──
    norm_path = os.path.join(stats_dir, "normalized_counts.csv")
    deg_results = _run_deseq2(
        counts_df,
        metadata,
        column_mapping=column_mapping,
        contrasts_list=contrasts_list,
        stats_dir=stats_dir,
        norm_output=norm_path,
        adj_pvalue_cutoff=submission.adjusted_pvalue,
        min_log2fc=submission.min_log2fc,
        max_log2fc=submission.max_log2fc,
    )

    # ── Step 5: Generate interactive plot data ──
    norm_df = pd.read_csv(norm_path)
    plot_data = _generate_plot_data(
        norm_df, metadata, deg_results, primary_group,
        adj_pvalue_cutoff=submission.adjusted_pvalue,
        min_log2fc=submission.min_log2fc,
        max_log2fc=submission.max_log2fc,
    )

    return {
        "stats_dir": stats_dir,
        "deg_results": deg_results,
        "normalized_counts": norm_path,
        "outlier_flags": outlier_flags,
        "batch_corrected": has_batch,
        "primary_group": primary_group,
        "contrasts_used": contrasts_list,
        "plot_data": plot_data,
    }
