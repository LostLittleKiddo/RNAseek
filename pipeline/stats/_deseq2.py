"""DESeq2 execution — formula building, factor sanitisation, and contrast extraction."""

import logging
import os
import re

import numpy as np

import pipeline.stats._r_bridge as _rb
from pipeline.stats._r_bridge import _R_CORES, _ensure_rpy2

logger = logging.getLogger(__name__)


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

    Returns (sanitized_col_data, level_maps) where level_maps is
    {column_name: {sanitized: original, ...}}.
    """
    level_maps = {}
    sanitized = col_data.copy()

    for c in sanitized.columns:
        original_vals = sanitized[c].astype(str)
        unique_vals = original_vals.unique()

        orig_to_safe = {}
        for val in unique_vals:
            safe = re.sub(r'[^A-Za-z0-9_.\-]', '_', val)
            safe = re.sub(r'_+', '_', safe).strip('_')
            base = safe
            counter = 2
            while safe in orig_to_safe.values():
                safe = "%s_%d" % (base, counter)
                counter += 1
            orig_to_safe[val] = safe

        level_maps[c] = {v: k for k, v in orig_to_safe.items()}
        sanitized[c] = original_vals.map(orig_to_safe)

    return sanitized, level_maps


def _r_string_vector(items):
    """Build an R character vector string from a Python list.

    Escapes backslashes and double-quotes so values are safe inside R strings.
    """
    escaped = []
    for item in items:
        safe = str(item).replace("\\", "\\\\").replace('"', '\\"')
        escaped.append('"%s"' % safe)
    return ", ".join(escaped)


def _run_deseq2(counts_df, metadata, column_mapping, contrasts_list,
                stats_dir, norm_output, adj_pvalue_cutoff,
                min_log2fc, max_log2fc):
    """Run DESeq2 with a dynamically constructed formula and extract contrasts.

    Returns a list of result file paths.
    """
    _ensure_rpy2()
    import pandas as pd

    primary_group = column_mapping["primary_group"]
    formula_str = _build_formula_string(column_mapping)
    logger.info("DESeq2 design formula: %s", formula_str)

    with _rb.localconverter(_rb._converter):
        deseq2 = _rb.importr("DESeq2")
        base = _rb.importr("base")

        try:
            biocparallel = _rb.importr("BiocParallel")
            _rb.ro.r('register(MulticoreParam(%d))' % _R_CORES)
            logger.info("BiocParallel: using %d cores", _R_CORES)
        except Exception:
            logger.info("BiocParallel not available — DESeq2 will run single-threaded")

        # ── Prepare count matrix in R ──
        count_matrix_r = _rb.ro.r["as.matrix"](counts_df.values.astype(int))
        _rb.ro.r.assign("count_matrix", count_matrix_r)
        _rb.ro.r(
            'rownames(count_matrix) <- c(%s)' % _r_string_vector(counts_df.index.tolist())
        )
        _rb.ro.r(
            'colnames(count_matrix) <- c(%s)' % _r_string_vector(counts_df.columns.tolist())
        )

        # ── Build colData ──
        formula_cols = list(column_mapping.get("additional_covariates", []))
        if column_mapping.get("batch_effect"):
            formula_cols.append(column_mapping["batch_effect"])
        formula_cols.append(primary_group)

        col_data = metadata[formula_cols].copy()

        for c in col_data.columns:
            col_data[c] = col_data[c].astype(str)

        col_data, level_maps = _sanitize_factor_levels(col_data, column_mapping)

        # Reverse lookup: original -> sanitized
        primary_level_map = {v: k for k, v in level_maps.get(primary_group, {}).items()}

        _rb.ro.r.assign("col_data", col_data)

        for c in formula_cols:
            _rb.ro.r('col_data$%s <- as.factor(col_data$%s)' % (c, c))

        # ── Run DESeq2 ──
        _rb.ro.r('design_formula <- as.formula("%s")' % formula_str)

        try:
            _rb.ro.r('''
                dds <- DESeqDataSetFromMatrix(
                    countData = count_matrix,
                    colData = col_data,
                    design = design_formula
                )
                dds <- DESeq(dds, parallel = TRUE)
            ''')
        except Exception as exc:
            err_msg = str(exc)
            if "full rank" in err_msg.lower() or "rank" in err_msg.lower():
                raise RuntimeError(
                    "DESeq2 error: The model matrix is not full rank. "
                    "This typically means your experimental design has perfect "
                    "confounding between variables (e.g., batch and condition are "
                    "identical). Please review your metadata column assignments "
                    "and remove redundant or perfectly correlated variables."
                ) from exc
            if "dispersion" in err_msg.lower() and "gene-wise" in err_msg.lower():
                logger.warning(
                    "DESeq2 dispersion fit failed — falling back to gene-wise estimates."
                )
                _rb.ro.r('''
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
                raise RuntimeError(
                    "DESeq2 execution failed: %s" % err_msg
                ) from exc

        # ── Extract normalized counts ──
        _rb.ro.r('norm_counts <- counts(dds, normalized = TRUE)')
        _rb.ro.r('norm_df <- as.data.frame(norm_counts)')
        _rb.ro.r('norm_df$gene_id <- rownames(norm_df)')
        norm_df = _rb.ro.r("norm_df")
        norm_df.to_csv(norm_output, index=False)

        # ── Extract DEG results ──
        result_paths = []

        if contrasts_list:
            for pair in contrasts_list:
                target, reference = pair[0], pair[1]
                contrast_label = "%s_vs_%s" % (target, reference)
                safe_label = re.sub(r'[^\w\-.]', '_', contrast_label)
                deg_path = os.path.join(stats_dir, "deg_%s.csv" % safe_label)

                safe_target = primary_level_map.get(target, target)
                safe_reference = primary_level_map.get(reference, reference)

                try:
                    _rb.ro.r(
                        'res <- results(dds, contrast=c(%s, %s, %s))'
                        % (
                            _r_string_vector([primary_group]),
                            _r_string_vector([safe_target]),
                            _r_string_vector([safe_reference]),
                        )
                    )
                    _rb.ro.r('res_df <- as.data.frame(res)')
                    _rb.ro.r('res_df$gene_id <- rownames(res_df)')

                    res_df = _rb.ro.r("res_df")
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
            deg_path = os.path.join(stats_dir, "deg_results.csv")

            _rb.ro.r('res <- results(dds)')
            _rb.ro.r('res_df <- as.data.frame(res)')
            _rb.ro.r('res_df$gene_id <- rownames(res_df)')

            res_df = _rb.ro.r("res_df")
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
