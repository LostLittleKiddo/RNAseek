"""
WGCNA & Pathway Enrichment Engine
==================================
Runs Weighted Gene Co-expression Network Analysis (PyWGCNA) followed by
pathway enrichment (gseapy) on the top trait-correlated module's hub genes.

Pipeline steps:
    wgcna_load_data -> wgcna_find_modules -> wgcna_module_trait ->
    wgcna_hub_genes -> wgcna_enrichment -> wgcna_plots
"""

import logging
import os

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from pipeline.models import AnalysisJob
from pipeline.stats._plots_wgcna import build_module_trait_heatmap, build_pathway_dotplot
from pipeline.tasks._helpers import _emit_progress, _update_step

logger = logging.getLogger(__name__)

WGCNA_STEPS = [
    "wgcna_load_data",
    "wgcna_find_modules",
    "wgcna_module_trait",
    "wgcna_hub_genes",
    "wgcna_enrichment",
    "wgcna_plots",
]

DEFAULT_ENRICHR_LIBS = ["KEGG_2021_Human", "GO_Biological_Process_2023"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute_wgcna_and_pathways(
    job_id: str,
    session_id: str,
    matrix_path: str,
    metadata_path: str,
    *,
    soft_power: int | None = None,
    n_hub_genes: int = 30,
    enrichr_libraries: list[str] | None = None,
    traits_data: dict | None = None,
) -> dict:
    """
    End-to-end WGCNA -> GSEA pipeline.

    Parameters
    ----------
    job_id : str
        UUID of the AnalysisJob tracking this run.
    session_id : str
        UUID of the owning Session (for tenant isolation).
    matrix_path : str
        Absolute path to normalized count matrix CSV (genes x samples).
    metadata_path : str
        Absolute path to sample metadata CSV (samples x traits).
    n_hub_genes : int
        Number of top hub genes to extract from the lead module (default 30).
    enrichr_libraries : list[str] | None
        Enrichr gene-set libraries.  Falls back to KEGG + GO BP.

    Returns
    -------
    dict
        Result payload containing plot_data, hub_genes, enrichment_summary,
        top_module info, and module_gene_counts.
    """
    if enrichr_libraries is None:
        enrichr_libraries = list(DEFAULT_ENRICHR_LIBS)

    job = AnalysisJob.objects.get(job_id=job_id, session_id=session_id)

    # Initialise step tracker so the frontend progress bar knows the plan.
    job.step_progress = {
        "pipeline_steps": list(WGCNA_STEPS),
        "current_step": None,
        "completed_steps": [],
        "failed_step": None,
    }
    job.save(update_fields=["step_progress"])
    _emit_progress(job)

    # ------------------------------------------------------------------
    # Step 1 - Load & validate input data
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_load_data")

    counts_df, trait_df = _load_and_validate(matrix_path, metadata_path)

    # Merge manual/uploaded traits from the WGCNA modal form if provided.
    if traits_data:
        trait_df = _merge_traits_data(trait_df, traits_data)

    logger.info(
        "Loaded matrix %d genes x %d samples; metadata %d samples x %d traits",
        counts_df.shape[0], counts_df.shape[1],
        trait_df.shape[0], trait_df.shape[1],
    )

    # ------------------------------------------------------------------
    # Step 2 - PyWGCNA network construction & module detection
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_find_modules")

    work_dir = os.path.join(os.path.dirname(matrix_path), "wgcna")
    os.makedirs(work_dir, exist_ok=True)

    wgcna_obj, gene_module_df = _run_pywgcna(counts_df, work_dir, soft_power=soft_power)

    logger.info(
        "PyWGCNA detected %d modules across %d genes",
        gene_module_df["module"].nunique(),
        len(gene_module_df),
    )

    # ------------------------------------------------------------------
    # Step 3 - Module-trait correlation
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_module_trait")

    numeric_trait_df = _encode_traits(trait_df)
    cor_df, pval_df = _correlate_modules_traits(wgcna_obj, numeric_trait_df)

    # ------------------------------------------------------------------
    # Step 4 - Identify lead module -> extract hub genes
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_hub_genes")

    top_module, top_trait, top_pval = _find_top_module(cor_df, pval_df)
    hub_genes = _extract_hub_genes(
        wgcna_obj, gene_module_df, top_module, n_hub_genes,
    )

    logger.info(
        "Top module: %s (trait=%s, p=%.2e) -- %d hub genes extracted",
        top_module, top_trait, top_pval, len(hub_genes),
    )

    # ------------------------------------------------------------------
    # Step 5 - Pathway enrichment via Enrichr
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_enrichment")

    enrichment_df = _run_enrichr(hub_genes, enrichr_libraries)

    sig_count = (
        int((enrichment_df["Adjusted P-value"] < 0.05).sum())
        if not enrichment_df.empty else 0
    )
    logger.info("Enrichr returned %d significant terms (adj. p < 0.05)", sig_count)

    # ------------------------------------------------------------------
    # Step 6 - Build Plotly JSON payloads
    # ------------------------------------------------------------------
    _update_step(job, "wgcna_plots")

    heatmap_payload = build_module_trait_heatmap(cor_df, pval_df)
    dotplot_payload = build_pathway_dotplot(enrichment_df)

    # ------------------------------------------------------------------
    # Assemble final result
    # ------------------------------------------------------------------
    module_gene_counts = gene_module_df["module"].value_counts().to_dict()

    return {
        "plot_data": {
            "module_trait_heatmap": heatmap_payload,
            "pathway_dotplot": dotplot_payload,
        },
        "hub_genes": hub_genes,
        "top_module": top_module,
        "top_trait": top_trait,
        "top_module_pvalue": float(top_pval),
        "enrichment_summary": (
            enrichment_df.head(20).to_dict(orient="records")
            if not enrichment_df.empty else []
        ),
        "module_gene_counts": module_gene_counts,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _merge_traits_data(
    trait_df: pd.DataFrame,
    traits_data: dict,
) -> pd.DataFrame:
    """Merge user-supplied traits from the WGCNA modal into the trait frame.

    Supports ``{mode: "manual", data: [{col: val, ...}, ...]}`` payloads
    sent by the frontend builder.  Rows are matched on the trait_df index
    (sample IDs); unmatched rows are silently dropped.
    """
    if not isinstance(traits_data, dict):
        return trait_df

    mode = traits_data.get("mode")
    if mode == "manual":
        rows = traits_data.get("data")
        if not rows or not isinstance(rows, list):
            return trait_df
        extra = pd.DataFrame(rows)
        # Identify the sample-name column (first column or "Sample_ID").
        id_col = None
        for candidate in ("Sample_ID", "sample", "_sample_name"):
            if candidate in extra.columns:
                id_col = candidate
                break
        if id_col is None:
            id_col = extra.columns[0]
        extra = extra.set_index(id_col)
        # Keep only samples present in the existing trait_df.
        shared = trait_df.index.intersection(extra.index)
        if len(shared) == 0:
            logger.warning("traits_data has no samples overlapping metadata – ignored")
            return trait_df
        # Merge new trait columns, overwriting on collision.
        combined = trait_df.loc[shared].copy()
        for col in extra.columns:
            combined[col] = extra.loc[shared, col]
        return combined

    # Other modes (e.g. CSV upload reference) are resolved upstream by
    # _dispatch_wgcna; nothing to do here.
    return trait_df


def _load_and_validate(
    matrix_path: str,
    metadata_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load normalized counts (genes x samples) and metadata (samples x traits).

    Validates that at least one sample name overlaps between the two frames
    and restricts both to the shared sample set.
    """
    counts_df = pd.read_csv(matrix_path, index_col=0)
    trait_df = pd.read_csv(metadata_path, index_col=0)

    shared = counts_df.columns.intersection(trait_df.index)
    if len(shared) == 0:
        raise ValueError(
            "No overlapping sample IDs between the count-matrix columns "
            "and metadata rows.  Check that sample identifiers match."
        )

    return counts_df[shared], trait_df.loc[shared]


def _run_pywgcna(
    counts_df: pd.DataFrame,
    work_dir: str,
    *,
    soft_power: int | None = None,
) -> tuple:
    """
    Build a weighted co-expression network and detect gene modules.

    PyWGCNA expects *samples x genes*, so we transpose the DESeq2-style
    matrix (genes x samples) and write a temporary CSV.

    If *soft_power* is provided the user's value is forced by restricting
    the power search space to that single value.  Otherwise PyWGCNA picks
    the optimal threshold automatically.
    """
    transposed_path = os.path.join(work_dir, "expression_for_wgcna.csv")
    counts_df.T.to_csv(transposed_path)

    import PyWGCNA  # lazy import: requires pywgcna package

    kwargs = dict(
        name="rnaseek_wgcna",
        species="homo sapiens",
        geneExpPath=transposed_path,
        outputPath=work_dir,
        save=True,
    )
    if soft_power is not None:
        kwargs["powers"] = [int(soft_power)]

    wgcna_obj = PyWGCNA.WGCNA(**kwargs)

    wgcna_obj.preprocess()
    wgcna_obj.findModules()

    gene_module_df = pd.DataFrame({
        "gene": wgcna_obj.datExpr.var_names.tolist(),
        "module": wgcna_obj.datExpr.var["moduleColors"].tolist(),
    })

    return wgcna_obj, gene_module_df


def _encode_traits(trait_df: pd.DataFrame) -> pd.DataFrame:
    """One-hot encode categorical metadata columns for Pearson correlation.

    Numeric columns are passed through unchanged.  Categorical columns
    become binary indicator columns so correlation with module eigengenes
    is mathematically valid.
    """
    parts: list[pd.DataFrame] = []
    for col in trait_df.columns:
        if pd.api.types.is_numeric_dtype(trait_df[col]):
            parts.append(trait_df[[col]])
        else:
            dummies = pd.get_dummies(trait_df[col], prefix=col, dtype=float)
            parts.append(dummies)
    return pd.concat(parts, axis=1)


def _correlate_modules_traits(
    wgcna_obj,
    numeric_trait_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Pearson-correlate every module eigengene with every numeric trait.

    Returns (cor_df, pval_df) shaped (n_modules x n_traits).
    """
    me_df: pd.DataFrame = wgcna_obj.datME

    shared = me_df.index.intersection(numeric_trait_df.index)
    if len(shared) < 3:
        raise ValueError(
            f"Only {len(shared)} samples overlap between eigengenes and "
            "traits — need at least 3 for Pearson correlation."
        )
    me_df = me_df.loc[shared]
    trait_vals = numeric_trait_df.loc[shared]

    cor_matrix = pd.DataFrame(
        np.nan, index=me_df.columns, columns=trait_vals.columns,
    )
    pval_matrix = cor_matrix.copy()

    for me_col in me_df.columns:
        for tr_col in trait_vals.columns:
            r, p = scipy_stats.pearsonr(
                me_df[me_col].values,
                trait_vals[tr_col].values,
            )
            cor_matrix.loc[me_col, tr_col] = r
            pval_matrix.loc[me_col, tr_col] = p

    return cor_matrix, pval_matrix


def _find_top_module(
    cor_df: pd.DataFrame,
    pval_df: pd.DataFrame,
) -> tuple[str, str, float]:
    """Return (module_colour, trait_name, p_value) for the strongest hit.

    The *grey* module (unassigned genes) is excluded because it is not a
    coherent biological signal.
    """
    filtered = pval_df.drop(
        index=[i for i in pval_df.index if "grey" in i.lower()],
        errors="ignore",
    )
    if filtered.empty:
        raise ValueError("No non-grey modules detected by WGCNA.")

    min_idx = filtered.stack().idxmin()      # (ME_name, trait_name)
    me_name, trait_name = min_idx
    p_value = float(filtered.loc[me_name, trait_name])

    # "MEblue" -> "blue"
    module_colour = me_name[2:] if me_name.startswith("ME") else me_name
    return module_colour, trait_name, p_value


def _extract_hub_genes(
    wgcna_obj,
    gene_module_df: pd.DataFrame,
    target_module: str,
    n_hub: int,
) -> list[str]:
    """
    Rank genes inside *target_module* by module membership (kME) and
    return the top *n_hub* hub gene symbols.
    """
    module_genes = gene_module_df.loc[
        gene_module_df["module"] == target_module, "gene"
    ].tolist()

    if not module_genes:
        raise ValueError(f"No genes found in module '{target_module}'.")

    expr_df = pd.DataFrame(
        wgcna_obj.datExpr.X,
        index=wgcna_obj.datExpr.obs_names,
        columns=wgcna_obj.datExpr.var_names,
    )

    me_col = f"ME{target_module}"
    if me_col not in wgcna_obj.datME.columns:
        raise ValueError(
            f"Module eigengene '{me_col}' not found.  "
            f"Available: {list(wgcna_obj.datME.columns)}"
        )
    me_values = wgcna_obj.datME[me_col].values

    # Vectorised kME: correlate every module gene with the eigengene.
    available = [g for g in module_genes if g in expr_df.columns]
    if not available:
        raise ValueError(
            f"None of the {len(module_genes)} genes in module "
            f"'{target_module}' are present in the expression matrix."
        )

    gene_matrix = expr_df[available].values                # (n_samples, n_genes)
    # Reshape me_values to 2-D (1, n_samples) so np.vstack is unambiguous.
    me_row = me_values.reshape(1, -1)
    combined = np.vstack([me_row, gene_matrix.T])          # (n_genes+1, n_samples)
    cor_full = np.corrcoef(combined)                       # (n_genes+1, n_genes+1)
    kme_values = cor_full[0, 1:]                           # correlations with ME

    # Sort descending by |kME|; cap at actual gene count.
    order = np.argsort(-np.abs(kme_values))
    hub_genes = [available[i] for i in order[:min(n_hub, len(available))]]

    return hub_genes


def _run_enrichr(
    hub_genes: list[str],
    libraries: list[str],
) -> pd.DataFrame:
    """
    Run Enrichr over-representation analysis on hub genes.

    Returns a sorted DataFrame with parsed Overlap_count / Overlap_size
    columns ready for the dot-plot serializer.
    """
    if not hub_genes:
        return pd.DataFrame()

    import gseapy  # lazy import

    enr = gseapy.enrichr(
        gene_list=hub_genes,
        gene_sets=libraries,
        organism="human",
        outdir=None,
        no_plot=True,
        cutoff=0.05,
    )

    results: pd.DataFrame = enr.results.copy()
    if results.empty:
        return results

    # Parse "3/50" -> (matched genes, gene-set size) for the dot-plot.
    if "Overlap" in results.columns:
        parts = results["Overlap"].str.split("/", expand=True)
        results["Overlap_count"] = parts[0].astype(int)
        results["Overlap_size"] = parts[1].astype(int)
    else:
        results["Overlap_count"] = 0
        results["Overlap_size"] = 0

    return results.sort_values("Adjusted P-value")
