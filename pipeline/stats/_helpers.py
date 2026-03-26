"""Stage-2 helpers: metadata loading, filtering, batch correction, outlier detection."""

import logging

import numpy as np

import pipeline.stats._r_bridge as _rb
from pipeline.stats._r_bridge import _ensure_rpy2

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
#  Metadata
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

    The column named 'sample' (case-insensitive) or '_sample_name'
    is treated as sample identifiers.  File extensions are stripped
    to match featureCounts column names.
    """
    if "_sample_name" in metadata.columns:
        sample_col = "_sample_name"
    else:
        sample_col_match = [c for c in metadata.columns if c.lower() == "sample"]
        if sample_col_match:
            sample_col = sample_col_match[0]
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
    metadata = metadata.drop_duplicates(subset="_match_key")
    metadata = metadata.set_index("_match_key")

    common = [c for c in counts_df.columns if c in metadata.index]
    if not common:
        raise RuntimeError(
            "No overlapping sample names between metadata and count matrix. "
            "Metadata samples: %s; Count matrix columns: %s"
            % (list(metadata.index[:5]), list(counts_df.columns[:5]))
        )
    counts_df = counts_df[common]
    metadata = metadata.loc[common]

    for drop_col in [sample_col, "_sample_name"]:
        if drop_col in metadata.columns:
            metadata = metadata.drop(columns=[drop_col])

    return metadata, counts_df


# ─────────────────────────────────────────────────────────────
#  Filtering
# ─────────────────────────────────────────────────────────────

def _filter_low_counts(counts_df, min_total=10):
    """Remove genes with total counts below threshold across all samples."""
    return counts_df[counts_df.sum(axis=1) >= min_total]


# ─────────────────────────────────────────────────────────────
#  Batch correction
# ─────────────────────────────────────────────────────────────

def _combat_seq(counts_df, metadata, batch_col, group_col, covariates=None):
    """Run sva::ComBat_seq in R for batch correction.

    Gracefully returns the original counts if the batch column has fewer
    than 2 distinct levels (nothing to correct).

    Parameters
    ----------
    covariates : list[str] | None
        Additional covariate column names to pass as ``covar_mod`` to
        ComBat_seq for improved batch-effect estimation.
    """
    _ensure_rpy2()
    import pandas as pd

    if metadata[batch_col].nunique() < 2:
        logger.info(
            "Batch column '%s' has only one level — skipping ComBat_seq.",
            batch_col,
        )
        return counts_df

    with _rb.localconverter(_rb._converter):
        sva = _rb.importr("sva")

        count_matrix_r = _rb.ro.r["as.matrix"](counts_df.values)
        batch_r = _rb.ro.IntVector(
            metadata[batch_col].astype("category").cat.codes.values + 1
        )
        group_r = _rb.ro.IntVector(
            metadata[group_col].astype("category").cat.codes.values + 1
        )

        combat_kwargs = {
            "batch": batch_r,
            "group": group_r,
        }

        # Build covariate model matrix for ComBat_seq if covariates are present
        if covariates:
            valid_covs = [c for c in covariates if c in metadata.columns]
            if valid_covs:
                covar_df = metadata[valid_covs].copy()
                for c in covar_df.columns:
                    try:
                        covar_df[c] = pd.to_numeric(covar_df[c])
                    except (ValueError, TypeError):
                        covar_df[c] = covar_df[c].astype("category").cat.codes.values
                covar_r = _rb.ro.r["as.matrix"](covar_df.values)
                combat_kwargs["covar_mod"] = covar_r

        corrected_r = sva.ComBat_seq(count_matrix_r, **combat_kwargs)

        corrected = np.array(corrected_r)
    return pd.DataFrame(
        corrected,
        index=counts_df.index,
        columns=counts_df.columns,
    )


# ─────────────────────────────────────────────────────────────
#  Outlier detection
# ─────────────────────────────────────────────────────────────

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
