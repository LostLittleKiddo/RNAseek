"""Plot data generation for interactive Plotly.js visualisations."""

import logging
import re

import numpy as np

logger = logging.getLogger(__name__)


def _generate_plot_data(norm_df, metadata, deg_result_paths, primary_group,
                        adj_pvalue_cutoff=0.05, min_log2fc=-1.0, max_log2fc=1.0):
    """Generate JSON-serializable data for PCA, UMAP, Volcano, and MA plots."""
    import pandas as pd

    plot_data = {}

    if "gene_id" in norm_df.columns:
        sample_cols = [c for c in norm_df.columns if c != "gene_id"]
        norm_matrix = norm_df.set_index("gene_id")[sample_cols]
    else:
        norm_matrix = norm_df.set_index(norm_df.columns[0])
        sample_cols = list(norm_matrix.columns)

    group_map = _build_group_map(metadata, primary_group, sample_cols)

    try:
        plot_data["pca"] = _compute_pca_data(norm_matrix, group_map)
    except Exception as exc:
        logger.warning("PCA plot data generation failed: %s", exc)
        plot_data["pca"] = None

    try:
        plot_data["umap"] = _compute_umap_data(norm_matrix, group_map)
    except Exception as exc:
        logger.warning("UMAP plot data generation failed: %s", exc)
        plot_data["umap"] = None

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

    if "_match_key" in metadata.index.name or metadata.index.name == "_match_key":
        for sample in sample_cols:
            if sample in metadata.index:
                group_map[sample] = str(metadata.loc[sample, primary_group])
            else:
                group_map[sample] = "unknown"
    else:
        sample_col = metadata.columns[0]
        meta_dict = {}
        for _, row in metadata.iterrows():
            key = str(row.get(sample_col, row.get("_sample_name", "")))
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
    df = df[df["padj"] > 0]

    neg_log10_padj = -np.log10(df["padj"].values)
    log2fc = df["log2FoldChange"].values

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
