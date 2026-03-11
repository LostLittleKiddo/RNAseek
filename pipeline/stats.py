"""Stage 2: Dynamic DESeq2 with Flexible Experimental Designs.

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
import re
import warnings

import numpy as np

# Number of cores available for R parallelism
_R_CORES = max(2, (os.cpu_count() or 4) // 2)

# rpy2 emits a UserWarning about SIGINT when R is initialized from a non-main
# thread (e.g. a Celery worker). The warning is harmless — it only means
# Ctrl-C won't interrupt the embedded R interpreter from that thread, which
# is acceptable in a background task context.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="R is not initialized by the main thread",
        category=UserWarning,
    )
    import rpy2.robjects as ro
    from rpy2.robjects import numpy2ri, pandas2ri
    from rpy2.robjects.conversion import localconverter
    from rpy2.robjects.packages import importr

logger = logging.getLogger(__name__)

# Combined converter for numpy + pandas <-> R
_converter = ro.default_converter + numpy2ri.converter + pandas2ri.converter


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

    # ── Load count matrix ──
    counts_df = pd.read_csv(count_matrix_path, index_col=0)

    # ── Load metadata ──
    metadata = _load_metadata(submission)
    if metadata is None:
        raise RuntimeError("Could not load metadata for statistical analysis.")

    # ── Extract column mapping and contrasts from payload ──
    payload = submission.metadata_payload or {}
    column_mapping = payload.get("column_mapping", {})
    contrasts_list = payload.get("contrasts", [])

    primary_group = column_mapping.get("primary_group")
    batch_effect = column_mapping.get("batch_effect")
    additional_covariates = column_mapping.get("additional_covariates", [])

    if not primary_group:
        raise RuntimeError(
            "No primary_group column specified in column_mapping. "
            "Cannot run differential expression without a grouping variable."
        )

    # Align metadata samples with count matrix columns
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
    import pandas as pd

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


# ─────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────


def _load_metadata(submission):
    """Load metadata from the parsed payload embedded in metadata_payload.samples.

    Both 'upload' and 'manual' modes send parsed sample rows in the JSON payload,
    so there is no need to read a CSV file from disk.
    """
    import pandas as pd

    payload = submission.metadata_payload or {}
    samples = payload.get("samples", [])
    if not samples:
        return None
    return pd.DataFrame(samples)


def _align_samples(metadata, counts_df):
    """Ensure metadata rows match count matrix columns.

    The first column of metadata (or '_sample_name' for manual mode)
    is treated as sample identifiers. File extensions are stripped
    to match featureCounts column names.
    """
    # Determine the sample identifier column
    if "_sample_name" in metadata.columns:
        sample_col = "_sample_name"
    else:
        sample_col = metadata.columns[0]

    metadata = metadata.copy()
    metadata["_match_key"] = (
        metadata[sample_col]
        .astype(str)
        .str.replace(r"\.(fq|fastq)\.gz$", "", regex=True)
        .str.replace(r"_R[12]$", "", regex=True)
        .str.replace(r"_[12]$", "", regex=True)
    )
    # Remove duplicate sample entries (paired-end -> one row per sample)
    metadata = metadata.drop_duplicates(subset="_match_key")
    metadata = metadata.set_index("_match_key")

    # Align columns
    common = [c for c in counts_df.columns if c in metadata.index]
    if not common:
        raise RuntimeError(
            "No overlapping sample names between metadata and count matrix. "
            "Metadata samples: %s; Count matrix columns: %s"
            % (list(metadata.index[:5]), list(counts_df.columns[:5]))
        )
    counts_df = counts_df[common]
    metadata = metadata.loc[common]

    # Drop helper columns that shouldn't be in the design
    for drop_col in [sample_col, "_sample_name"]:
        if drop_col in metadata.columns:
            metadata = metadata.drop(columns=[drop_col])

    return metadata, counts_df


def _filter_low_counts(counts_df, min_total=10):
    """Remove genes with total counts below threshold across all samples."""
    return counts_df[counts_df.sum(axis=1) >= min_total]


def _combat_seq(counts_df, metadata, batch_col, group_col):
    """Run sva::ComBat_seq in R for batch correction."""
    import pandas as pd

    with localconverter(_converter):
        sva = importr("sva")

        count_matrix_r = ro.r["as.matrix"](counts_df.values)
        batch_r = ro.IntVector(
            metadata[batch_col].astype("category").cat.codes.values + 1
        )
        group_r = ro.IntVector(
            metadata[group_col].astype("category").cat.codes.values + 1
        )

        corrected_r = sva.ComBat_seq(
            count_matrix_r,
            batch=batch_r,
            group=group_r,
        )

        corrected = np.array(corrected_r)
    return pd.DataFrame(
        corrected,
        index=counts_df.index,
        columns=counts_df.columns,
    )


def _detect_outliers(counts_df, confidence=0.95):
    """Mahalanobis distance outlier detection on PCA of log-transformed counts.

    Returns dict mapping sample names to outlier status (True/False).
    """
    from scipy.spatial.distance import mahalanobis
    from scipy.stats import chi2
    from sklearn.decomposition import PCA

    log_counts = np.log2(counts_df.values.T + 1)

    n_components = min(5, log_counts.shape[0] - 1, log_counts.shape[1])
    if n_components < 2:
        return {s: False for s in counts_df.columns}

    pca = PCA(n_components=n_components)
    scores = pca.fit_transform(log_counts)

    mean = scores.mean(axis=0)
    cov = np.cov(scores, rowvar=False)

    # Regularize covariance to avoid singular matrix
    cov += np.eye(cov.shape[0]) * 1e-6
    cov_inv = np.linalg.inv(cov)

    threshold = chi2.ppf(confidence, df=n_components)

    flags = {}
    for i, sample in enumerate(counts_df.columns):
        dist = mahalanobis(scores[i], mean, cov_inv)
        flags[sample] = bool(dist > np.sqrt(threshold))

    return flags


def _build_formula_string(column_mapping):
    """Build a DESeq2-compatible R formula string from column_mapping.

    Order: ~ covariates + batch_effect + primary_group
    Primary group is ALWAYS last so DESeq2 extracts its contrasts by default.
    """
    terms = []

    for cov in column_mapping.get("additional_covariates", []):
        terms.append(cov)

    batch = column_mapping.get("batch_effect")
    if batch:
        terms.append(batch)

    primary = column_mapping["primary_group"]
    terms.append(primary)

    return "~ " + " + ".join(terms)


def _sanitize_factor_levels(col_data, column_mapping):
    """Sanitize factor level values to be safe in R.

    Special characters like %, (, ), spaces etc. can break R string
    interpolation and factor level matching. This function replaces
    problematic characters with underscores and returns a mapping
    from sanitized names back to originals for each column.

    Returns (sanitized_col_data, level_maps) where level_maps is
    {column_name: {sanitized: original, ...}}.
    """
    level_maps = {}
    sanitized = col_data.copy()

    for c in sanitized.columns:
        original_vals = sanitized[c].astype(str)
        unique_vals = original_vals.unique()

        # Build mapping: original -> sanitized
        orig_to_safe = {}
        for val in unique_vals:
            # Replace non-alphanumeric chars (except _.-) with underscore
            safe = re.sub(r'[^A-Za-z0-9_.\-]', '_', val)
            # Collapse runs of underscores
            safe = re.sub(r'_+', '_', safe).strip('_')
            # Ensure uniqueness by appending suffix if needed
            base = safe
            counter = 2
            while safe in orig_to_safe.values():
                safe = "%s_%d" % (base, counter)
                counter += 1
            orig_to_safe[val] = safe

        # Reverse map for contrasts: sanitized -> original
        level_maps[c] = {v: k for k, v in orig_to_safe.items()}

        # Apply sanitization
        sanitized[c] = original_vals.map(orig_to_safe)

    return sanitized, level_maps


def _run_deseq2(counts_df, metadata, column_mapping, contrasts_list,
                stats_dir, norm_output, adj_pvalue_cutoff,
                min_log2fc, max_log2fc):
    """Run DESeq2 with a dynamically constructed formula and extract contrasts.

    If contrasts_list is provided (multi-group), iterate through each
    contrast pair and save separate CSV results.

    Returns a list of result file paths.
    """
    import pandas as pd

    primary_group = column_mapping["primary_group"]
    formula_str = _build_formula_string(column_mapping)
    logger.info("DESeq2 design formula: %s", formula_str)

    with localconverter(_converter):
        deseq2 = importr("DESeq2")
        base = importr("base")

        # Register BiocParallel backend for DESeq2 parallelism
        try:
            biocparallel = importr("BiocParallel")
            ro.r('register(MulticoreParam(%d))' % _R_CORES)
            logger.info("BiocParallel: using %d cores", _R_CORES)
        except Exception:
            logger.info("BiocParallel not available — DESeq2 will run single-threaded")

        # ── Prepare count matrix in R ──
        count_matrix_r = ro.r["as.matrix"](counts_df.values.astype(int))
        ro.r.assign("count_matrix", count_matrix_r)
        ro.r(
            'rownames(count_matrix) <- c(%s)' % _r_string_vector(counts_df.index.tolist())
        )
        ro.r(
            'colnames(count_matrix) <- c(%s)' % _r_string_vector(counts_df.columns.tolist())
        )

        # ── Build colData from metadata ──
        # Only include columns referenced in the formula
        formula_cols = list(column_mapping.get("additional_covariates", []))
        if column_mapping.get("batch_effect"):
            formula_cols.append(column_mapping["batch_effect"])
        formula_cols.append(primary_group)

        col_data = metadata[formula_cols].copy()

        # Ensure all columns are factors (character) for DESeq2
        for c in col_data.columns:
            col_data[c] = col_data[c].astype(str)

        # Sanitize factor levels to avoid R issues with special characters
        col_data, level_maps = _sanitize_factor_levels(col_data, column_mapping)

        # Build reverse lookup for primary group: original -> sanitized
        primary_level_map = {v: k for k, v in level_maps.get(primary_group, {}).items()}

        ro.r.assign("col_data", col_data)

        # Convert all columns to factors in R
        for c in formula_cols:
            ro.r('col_data$%s <- as.factor(col_data$%s)' % (c, c))

        # ── Create DESeqDataSet and run DESeq ──
        ro.r('design_formula <- as.formula("%s")' % formula_str)

        try:
            ro.r('''
                dds <- DESeqDataSetFromMatrix(
                    countData = count_matrix,
                    colData = col_data,
                    design = design_formula
                )
                dds <- DESeq(dds, parallel = TRUE)
            ''')
        except Exception as exc:
            err_msg = str(exc)
            # Catch the notorious "model matrix is not full rank" error
            if "full rank" in err_msg.lower() or "rank" in err_msg.lower():
                raise RuntimeError(
                    "DESeq2 error: The model matrix is not full rank. "
                    "This typically means your experimental design has perfect "
                    "confounding between variables (e.g., batch and condition are "
                    "identical). Please review your metadata column assignments "
                    "and remove redundant or perfectly correlated variables."
                ) from exc
            # Dispersion fit failure: fall back to gene-wise estimates
            if "dispersion" in err_msg.lower() and "gene-wise" in err_msg.lower():
                logger.warning(
                    "DESeq2 dispersion fit failed — falling back to gene-wise estimates."
                )
                ro.r('''
                    dds <- DESeqDataSetFromMatrix(
                        countData = count_matrix,
                        colData = col_data,
                        design = design_formula
                    )
                    dds <- estimateSizeFactors(dds)
                    dds <- estimateDispersionsGeneEst(dds)
                    dispersions(dds) <- mcols(dds)$dispGeneEst
                    dds <- nbinomWaldTest(dds)
                ''')
            else:
                # Re-raise other errors with context
                raise RuntimeError(
                    "DESeq2 execution failed: %s" % err_msg
                ) from exc

        # ── Extract normalized counts ──
        ro.r('norm_counts <- counts(dds, normalized = TRUE)')
        ro.r('norm_df <- as.data.frame(norm_counts)')
        ro.r('norm_df$gene_id <- rownames(norm_df)')
        norm_df = ro.r("norm_df")
        norm_df.to_csv(norm_output, index=False)

        # ── Extract DEG results ──
        result_paths = []

        if contrasts_list:
            # Multi-contrast mode: extract results for each specified contrast
            for pair in contrasts_list:
                target, reference = pair[0], pair[1]
                contrast_label = "%s_vs_%s" % (target, reference)
                # Sanitize label for filesystem safety (remove /, %, etc.)
                safe_label = re.sub(r'[^\w\-.]', '_', contrast_label)
                deg_path = os.path.join(stats_dir, "deg_%s.csv" % safe_label)

                # Map original contrast values to sanitized factor levels
                safe_target = primary_level_map.get(target, target)
                safe_reference = primary_level_map.get(reference, reference)

                try:
                    ro.r(
                        'res <- results(dds, contrast=c(%s, %s, %s))'
                        % (
                            _r_string_vector([primary_group]),
                            _r_string_vector([safe_target]),
                            _r_string_vector([safe_reference]),
                        )
                    )
                    ro.r('res_df <- as.data.frame(res)')
                    ro.r('res_df$gene_id <- rownames(res_df)')

                    res_df = ro.r("res_df")
                    res_df["contrast"] = contrast_label
                    res_df["significant"] = (
                        (res_df["padj"] <= adj_pvalue_cutoff)
                        & (
                            (res_df["log2FoldChange"] <= min_log2fc)
                            | (res_df["log2FoldChange"] >= max_log2fc)
                        )
                    )
                    res_df.to_csv(deg_path, index=False)
                    result_paths.append(deg_path)

                    logger.info(
                        "Contrast %s: %d genes, %d significant",
                        contrast_label,
                        len(res_df),
                        res_df["significant"].sum(),
                    )
                except Exception as exc:
                    logger.error(
                        "Failed to extract contrast %s: %s", contrast_label, exc
                    )
                    raise RuntimeError(
                        "Failed to extract DESeq2 results for contrast %s: %s"
                        % (contrast_label, exc)
                    ) from exc
        else:
            # Default mode (2 groups): extract the single default comparison
            deg_path = os.path.join(stats_dir, "deg_results.csv")

            ro.r('res <- results(dds)')
            ro.r('res_df <- as.data.frame(res)')
            ro.r('res_df$gene_id <- rownames(res_df)')

            res_df = ro.r("res_df")
            res_df["significant"] = (
                (res_df["padj"] <= adj_pvalue_cutoff)
                & (
                    (res_df["log2FoldChange"] <= min_log2fc)
                    | (res_df["log2FoldChange"] >= max_log2fc)
                )
            )
            res_df.to_csv(deg_path, index=False)
            result_paths.append(deg_path)

            logger.info(
                "DESeq2 complete: %d genes, %d significant",
                len(res_df),
                res_df["significant"].sum(),
            )

    return result_paths


def _r_string_vector(items):
    """Build an R character vector string from a Python list.

    Escapes backslashes and double-quotes so values are safe inside R strings.
    """
    escaped = []
    for item in items:
        safe = str(item).replace("\\", "\\\\").replace('"', '\\"')
        escaped.append('"%s"' % safe)
    return ", ".join(escaped)


# ─────────────────────────────────────────────────────────────
#  Plot data generation for interactive Plotly.js visualizations
# ─────────────────────────────────────────────────────────────


def _generate_plot_data(norm_df, metadata, deg_result_paths, primary_group,
                        adj_pvalue_cutoff=0.05, min_log2fc=-1.0, max_log2fc=1.0):
    """Generate JSON-serializable data for PCA, UMAP, Volcano, and MA plots."""
    import pandas as pd

    plot_data = {}

    # norm_df has gene_id column + sample columns from DESeq2 normalized counts
    if "gene_id" in norm_df.columns:
        sample_cols = [c for c in norm_df.columns if c != "gene_id"]
        norm_matrix = norm_df.set_index("gene_id")[sample_cols]
    else:
        norm_matrix = norm_df.set_index(norm_df.columns[0])
        sample_cols = list(norm_matrix.columns)

    # Build sample->group mapping from metadata
    group_map = _build_group_map(metadata, primary_group, sample_cols)

    # ── PCA plot data ──
    try:
        plot_data["pca"] = _compute_pca_data(norm_matrix, group_map)
    except Exception as exc:
        logger.warning("PCA plot data generation failed: %s", exc)
        plot_data["pca"] = None

    # ── UMAP plot data ──
    try:
        plot_data["umap"] = _compute_umap_data(norm_matrix, group_map)
    except Exception as exc:
        logger.warning("UMAP plot data generation failed: %s", exc)
        plot_data["umap"] = None

    # ── Volcano and MA plot data (from first DEG result file) ──
    if deg_result_paths:
        try:
            deg_df = pd.read_csv(deg_result_paths[0])
            plot_data["volcano"] = _compute_volcano_data(
                deg_df, adj_pvalue_cutoff, min_log2fc, max_log2fc
            )
            plot_data["ma"] = _compute_ma_data(
                deg_df, adj_pvalue_cutoff, min_log2fc, max_log2fc
            )
        except Exception as exc:
            logger.warning("Volcano/MA plot data generation failed: %s", exc)
            plot_data["volcano"] = None
            plot_data["ma"] = None
    else:
        plot_data["volcano"] = None
        plot_data["ma"] = None

    return plot_data


def _build_group_map(metadata, primary_group, sample_cols):
    """Map sample names to their group labels from metadata."""
    group_map = {}
    if primary_group not in metadata.columns:
        return {s: "unknown" for s in sample_cols}

    # Determine sample identifier column in metadata
    if "_match_key" in metadata.index.name or metadata.index.name == "_match_key":
        for sample in sample_cols:
            if sample in metadata.index:
                group_map[sample] = str(metadata.loc[sample, primary_group])
            else:
                group_map[sample] = "unknown"
    else:
        # Try first column as sample identifier
        sample_col = metadata.columns[0]
        meta_dict = {}
        for _, row in metadata.iterrows():
            key = str(row.get(sample_col, row.get("_sample_name", "")))
            # Strip extensions for matching
            import re
            clean_key = re.sub(r'\.(fq|fastq)\.gz$', '', key)
            clean_key = re.sub(r'_R[12]$', '', clean_key)
            clean_key = re.sub(r'_[12]$', '', clean_key)
            meta_dict[clean_key] = str(row[primary_group])

        for sample in sample_cols:
            group_map[sample] = meta_dict.get(sample, "unknown")

    return group_map


def _compute_pca_data(norm_matrix, group_map):
    """Compute PCA coordinates for samples."""
    from sklearn.decomposition import PCA

    # Log-transform and transpose: samples as rows, genes as columns
    log_data = np.log2(norm_matrix.values.T + 1)
    n_components = min(2, log_data.shape[0] - 1, log_data.shape[1])
    if n_components < 2:
        return None

    pca = PCA(n_components=2)
    coords = pca.fit_transform(log_data)
    samples = list(norm_matrix.columns)

    return {
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "samples": samples,
        "groups": [group_map.get(s, "unknown") for s in samples],
        "var_explained": [float(v) * 100 for v in pca.explained_variance_ratio_[:2]],
    }


def _compute_umap_data(norm_matrix, group_map):
    """Compute UMAP coordinates for samples."""
    from sklearn.decomposition import PCA

    log_data = np.log2(norm_matrix.values.T + 1)
    n_samples = log_data.shape[0]

    if n_samples < 4:
        return None

    # Pre-reduce with PCA before UMAP for stability
    n_pca = min(10, n_samples - 1, log_data.shape[1])
    pca = PCA(n_components=n_pca)
    pca_data = pca.fit_transform(log_data)

    try:
        import umap
        n_neighbors = min(15, n_samples - 1)
        reducer = umap.UMAP(n_components=2, n_neighbors=n_neighbors, random_state=42)
        coords = reducer.fit_transform(pca_data)
    except ImportError:
        logger.info("umap-learn not installed — skipping UMAP plot")
        return None

    samples = list(norm_matrix.columns)
    return {
        "x": [float(v) for v in coords[:, 0]],
        "y": [float(v) for v in coords[:, 1]],
        "samples": samples,
        "groups": [group_map.get(s, "unknown") for s in samples],
    }


def _compute_volcano_data(deg_df, adj_pvalue_cutoff, min_log2fc, max_log2fc):
    """Compute volcano plot data: log2FC vs -log10(padj)."""
    df = deg_df.dropna(subset=["log2FoldChange", "padj"]).copy()
    df = df[df["padj"] > 0]  # avoid log(0)

    neg_log10_padj = -np.log10(df["padj"].values)
    log2fc = df["log2FoldChange"].values

    # Classify: up, down, not significant
    is_sig = df["padj"].values <= adj_pvalue_cutoff
    is_up = log2fc >= max_log2fc
    is_down = log2fc <= min_log2fc

    categories = []
    for i in range(len(df)):
        if is_sig[i] and is_up[i]:
            categories.append("up")
        elif is_sig[i] and is_down[i]:
            categories.append("down")
        else:
            categories.append("ns")

    gene_ids = df["gene_id"].tolist() if "gene_id" in df.columns else df.index.tolist()

    return {
        "log2fc": [float(v) for v in log2fc],
        "neg_log10_padj": [float(v) for v in neg_log10_padj],
        "genes": gene_ids,
        "categories": categories,
        "thresholds": {
            "padj": adj_pvalue_cutoff,
            "log2fc_up": max_log2fc,
            "log2fc_down": min_log2fc,
        },
    }


def _compute_ma_data(deg_df, adj_pvalue_cutoff, min_log2fc, max_log2fc):
    """Compute MA plot data: baseMean (A) vs log2FC (M)."""
    df = deg_df.dropna(subset=["baseMean", "log2FoldChange"]).copy()
    df = df[df["baseMean"] > 0]

    log_base_mean = np.log10(df["baseMean"].values)
    log2fc = df["log2FoldChange"].values

    is_sig = (df["padj"].values <= adj_pvalue_cutoff) & (
        (log2fc <= min_log2fc) | (log2fc >= max_log2fc)
    )

    gene_ids = df["gene_id"].tolist() if "gene_id" in df.columns else df.index.tolist()

    return {
        "log_base_mean": [float(v) for v in log_base_mean],
        "log2fc": [float(v) for v in log2fc],
        "genes": gene_ids,
        "significant": [bool(v) for v in is_sig],
    }
