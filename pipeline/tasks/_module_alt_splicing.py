"""
Alternative Splicing Engine (IsoformSwitchAnalyzeR)
====================================================
Detects isoform switching events between two conditions using aligned BAM
files and a reference GTF annotation via the Bioconductor
IsoformSwitchAnalyzeR package.

Pipeline steps:
    splicing_load_data -> splicing_import_rdata -> splicing_analysis -> splicing_serialize
"""

import csv
import io
import logging
import os

import numpy as np
import pandas as pd

from pipeline.models import AnalysisJob
from pipeline.tasks._helpers import _emit_progress, _update_step

logger = logging.getLogger(__name__)

SPLICING_STEPS = [
    "splicing_load_data",
    "splicing_import_rdata",
    "splicing_analysis",
    "splicing_serialize",
]


def execute_alt_splicing(
    job_id: str,
    session_id: str,
    bam_paths: list[str],
    genome_gtf: str,
    sample_conditions: list[dict],
) -> dict:
    """
    End-to-end IsoformSwitchAnalyzeR alternative splicing analysis.

    Parameters
    ----------
    job_id : str
        UUID of the AnalysisJob tracking this run.
    session_id : str
        UUID of the owning Session (for tenant isolation).
    bam_paths : list[str]
        Absolute paths to sorted, indexed BAM files from the core pipeline.
    genome_gtf : str
        Absolute path to the reference GTF annotation file.
    sample_conditions : list[dict]
        Each dict has ``file_name`` (BAM basename) and ``condition`` (group label).

    Returns
    -------
    dict
        Result payload with ``summary``, ``table_preview``, and ``plot_data``.
    """
    job = AnalysisJob.objects.get(job_id=job_id, session_id=session_id)

    job.step_progress = {
        "pipeline_steps": list(SPLICING_STEPS),
        "current_step": None,
        "completed_steps": [],
        "failed_step": None,
    }
    job.save(update_fields=["step_progress"])
    _emit_progress(job)

    # ------------------------------------------------------------------
    # Step 1 — Load & validate inputs
    # ------------------------------------------------------------------
    _update_step(job, "splicing_load_data")

    condition_map = _build_condition_map(sample_conditions, bam_paths)
    logger.info(
        "Alt splicing: %d BAMs, %d conditions, GTF=%s",
        len(condition_map),
        len(set(condition_map.values())),
        os.path.basename(genome_gtf),
    )

    conditions = set(condition_map.values())
    if len(conditions) < 2:
        raise ValueError(
            f"At least two distinct conditions are required for splicing "
            f"analysis. Found: {', '.join(sorted(conditions))}"
        )

    _update_step(job, "splicing_load_data", completed=True)

    # ------------------------------------------------------------------
    # Step 2 — Import data into IsoformSwitchAnalyzeR via rpy2
    # ------------------------------------------------------------------
    _update_step(job, "splicing_import_rdata")

    switch_list = _import_rdata(condition_map, genome_gtf)

    _update_step(job, "splicing_import_rdata", completed=True)

    # ------------------------------------------------------------------
    # Step 3 — Run isoform switch analysis (Part 1)
    # ------------------------------------------------------------------
    _update_step(job, "splicing_analysis")

    result_df = _run_switch_analysis(switch_list)
    logger.info("IsoformSwitchAnalyzeR returned %d switch results.", len(result_df))

    _update_step(job, "splicing_analysis", completed=True)

    # ------------------------------------------------------------------
    # Step 4 — Serialize results into frontend-compatible JSON
    # ------------------------------------------------------------------
    _update_step(job, "splicing_serialize")

    payload = _build_result_payload(result_df)

    _update_step(job, "splicing_serialize", completed=True)

    return payload


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _build_condition_map(
    sample_conditions: list[dict],
    bam_paths: list[str],
) -> dict[str, str]:
    """Map absolute BAM paths to condition labels.

    The frontend sends ``file_name`` (BAM basename) and ``condition``.
    We match each to the actual absolute path from the core pipeline.
    """
    basename_to_path = {os.path.basename(p): p for p in bam_paths}

    condition_map: dict[str, str] = {}
    for entry in sample_conditions:
        fname = entry.get("file_name", "").strip()
        cond = entry.get("condition", "").strip()
        if not fname or not cond:
            continue
        abs_path = basename_to_path.get(fname)
        if abs_path is None:
            logger.warning(
                "BAM file '%s' from condition mapping not found among "
                "available BAMs: %s",
                fname, list(basename_to_path.keys()),
            )
            continue
        condition_map[abs_path] = cond

    if not condition_map:
        raise ValueError(
            "No valid BAM-to-condition mappings found. Ensure file_name "
            "entries match your uploaded BAM filenames."
        )
    return condition_map


def _parse_csv_conditions(csv_text: str) -> list[dict]:
    """Parse CSV text with ``file_name`` and ``condition`` columns."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = []
    for row in reader:
        fname = (
            row.get("file_name")
            or row.get("File_Name")
            or row.get("filename")
            or row.get("sample")
            or ""
        ).strip()
        cond = (
            row.get("condition")
            or row.get("Condition")
            or row.get("group")
            or ""
        ).strip()
        if fname and cond:
            rows.append({"file_name": fname, "condition": cond})
    return rows


def _import_rdata(condition_map: dict[str, str], genome_gtf: str):
    """Import BAMs + GTF into IsoformSwitchAnalyzeR via rpy2.

    Constructs a sample design DataFrame and calls ``importRdata()``
    to create a switchAnalyzeRlist object.
    """
    from pipeline.stats._r_bridge import (
        _converter,
        _ensure_rpy2,
        importr,
        localconverter,
        ro,
    )

    _ensure_rpy2()

    # Build the design matrix: sampleID, condition, bam_path
    samples = []
    for bam_path, condition in condition_map.items():
        sample_id = os.path.splitext(os.path.basename(bam_path))[0]
        samples.append({
            "sampleID": sample_id,
            "condition": condition,
        })

    design_df = pd.DataFrame(samples)

    # Build named BAM path vector for importRdata
    bam_paths_ordered = list(condition_map.keys())
    sample_ids = [os.path.splitext(os.path.basename(p))[0] for p in bam_paths_ordered]

    with localconverter(_converter):
        isar = importr("IsoformSwitchAnalyzeR")

        # Create R character vector of BAM paths named by sample ID
        r_bam_vec = ro.StrVector(bam_paths_ordered)
        r_bam_vec.names = ro.StrVector(sample_ids)

        r_design = ro.conversion.get_conversion().py2rpy(design_df)
        r_gtf = ro.StrVector([genome_gtf])

        # importRdata: quantify isoforms from BAMs using salmonTE-style
        # or direct BAM import via IsoformSwitchAnalyzeR's built-in importer
        switch_list = isar.importRdata(
            isoformCountMatrix=ro.NULL,
            isoformRepExpression=ro.NULL,
            designMatrix=r_design,
            isoformExonAnnoation=r_gtf[0],
            showProgress=False,
        )

    return switch_list


def _run_switch_analysis(switch_list) -> pd.DataFrame:
    """Run isoformSwitchAnalysisPart1 and extract the switch test results."""
    from pipeline.stats._r_bridge import (
        _R_CORES,
        _converter,
        _ensure_rpy2,
        importr,
        localconverter,
        ro,
    )

    _ensure_rpy2()

    with localconverter(_converter):
        isar = importr("IsoformSwitchAnalyzeR")

        # Run the core statistical analysis
        switch_analyzed = isar.isoformSwitchAnalysisPart1(
            switchAnalyzeRlist=switch_list,
            pathToOutput=ro.NULL,
            outputSequences=False,
            quiet=True,
        )

        # Extract the isoform switch test results
        r_result = isar.extractTopSwitches(
            switchAnalyzeRlist=switch_analyzed,
            filterForConsequences=False,
            n=ro.NA_Integer,
            sortByQvals=True,
        )

        result_df = ro.conversion.get_conversion().rpy2py(r_result)

    return result_df


def _build_result_payload(result_df: pd.DataFrame) -> dict:
    """Serialize IsoformSwitchAnalyzeR results into the standard payload.

    Returns a dict with:
        summary     — human-readable summary string
        table_preview — HTML table of top switches
        plot_data   — dict of Plotly-compatible chart payloads
    """
    result_df = _normalize_result_columns(result_df)

    # Sort by q-value (ascending)
    if "qvalue" in result_df.columns:
        result_df = result_df.sort_values("qvalue", na_position="last")

    sig_mask = result_df.get("qvalue", pd.Series(dtype=float)) < 0.05
    sig_count = int(sig_mask.sum()) if not sig_mask.empty else 0
    total = len(result_df)

    summary = (
        f"Identified {sig_count} significant isoform switching event"
        f"{'s' if sig_count != 1 else ''} at FDR < 0.05 "
        f"(out of {total} tested genes)."
    )

    # Build HTML table preview (top 50 rows)
    table_preview = _build_table_html(result_df.head(50))

    # Build Plotly visualizations
    plot_data = {}

    volcano = _build_volcano_plot(result_df)
    if volcano:
        plot_data["volcano"] = volcano

    switch_bar = _build_switch_type_bar(result_df)
    if switch_bar:
        plot_data["switch_types"] = switch_bar

    return {
        "summary": summary,
        "table_preview": table_preview,
        "plot_data": plot_data,
    }


def _normalize_result_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize column names from IsoformSwitchAnalyzeR output."""
    rename_map = {}
    for col in df.columns:
        lower = col.lower().replace(".", "_")
        if "gene" in lower and "id" in lower:
            rename_map[col] = "gene_id"
        elif "gene" in lower and "name" in lower:
            rename_map[col] = "gene_name"
        elif lower in ("gene_ref", "generef"):
            rename_map[col] = "gene_name"
        elif "isoform" in lower and "id" in lower:
            rename_map[col] = "isoform_id"
        elif lower.startswith("dif") or lower == "dif":
            rename_map[col] = "dIF"
        elif lower in ("padj", "q_value", "qvalue", "qval"):
            rename_map[col] = "qvalue"
        elif lower in ("pvalue", "p_value"):
            rename_map[col] = "pvalue"
        elif "condition_1" in lower or lower == "condition1":
            rename_map[col] = "condition_1"
        elif "condition_2" in lower or lower == "condition2":
            rename_map[col] = "condition_2"
        elif "switch_consequence" in lower:
            rename_map[col] = "switch_consequence"

    if rename_map:
        df = df.rename(columns=rename_map)
    return df


def _build_table_html(df: pd.DataFrame) -> str:
    """Build an HTML table string for the frontend preview."""
    display_cols = [
        c for c in ["gene_name", "gene_id", "condition_1", "condition_2",
                     "dIF", "pvalue", "qvalue"]
        if c in df.columns
    ]
    if not display_cols:
        display_cols = list(df.columns[:7])

    rows = []
    rows.append("<table class='md-example-table'><thead><tr>")
    for col in display_cols:
        pretty = col.replace("_", " ").title()
        rows.append(f"<th>{pretty}</th>")
    rows.append("</tr></thead><tbody>")

    for _, row in df[display_cols].iterrows():
        rows.append("<tr>")
        for col in display_cols:
            val = row[col]
            if isinstance(val, float):
                cell = f"{val:.4g}" if abs(val) < 1000 else f"{val:.2e}"
            else:
                cell = str(val) if pd.notna(val) else ""
            rows.append(f"<td>{cell}</td>")
        rows.append("</tr>")

    rows.append("</tbody></table>")
    return "".join(rows)


def _build_volcano_plot(df: pd.DataFrame) -> dict | None:
    """Build a Plotly volcano plot: dIF vs -log10(qvalue).

    dIF (delta Isoform Fraction) is the effect size in
    IsoformSwitchAnalyzeR — analogous to log2FC for splicing.
    """
    if "dIF" not in df.columns or "qvalue" not in df.columns:
        return None

    plot_df = df[["dIF", "qvalue"]].dropna().copy()
    if plot_df.empty:
        return None

    plot_df["neg_log10_q"] = -np.log10(plot_df["qvalue"].clip(lower=1e-300))

    # Classify significance
    sig_threshold = 0.05
    dif_threshold = 0.1
    is_sig = plot_df["qvalue"] < sig_threshold
    is_large = plot_df["dIF"].abs() > dif_threshold

    colors = []
    for s, l in zip(is_sig, is_large):
        if s and l:
            colors.append("#e74c3c")   # significant + large dIF
        elif s:
            colors.append("#f39c12")   # significant only
        else:
            colors.append("#95a5a6")   # not significant

    gene_labels = df.loc[plot_df.index, "gene_name"].tolist() if "gene_name" in df.columns else [""] * len(plot_df)

    trace = {
        "type": "scattergl",
        "mode": "markers",
        "x": plot_df["dIF"].tolist(),
        "y": plot_df["neg_log10_q"].tolist(),
        "text": gene_labels,
        "hovertemplate": (
            "Gene: %{text}<br>"
            "dIF: %{x:.3f}<br>"
            "-log10(q): %{y:.2f}<extra></extra>"
        ),
        "marker": {
            "color": colors,
            "size": 5,
            "opacity": 0.7,
        },
    }

    # Threshold lines
    shapes = [
        {
            "type": "line", "x0": -dif_threshold, "x1": -dif_threshold,
            "y0": 0, "y1": plot_df["neg_log10_q"].max() * 1.05,
            "line": {"color": "#bdc3c7", "dash": "dash", "width": 1},
        },
        {
            "type": "line", "x0": dif_threshold, "x1": dif_threshold,
            "y0": 0, "y1": plot_df["neg_log10_q"].max() * 1.05,
            "line": {"color": "#bdc3c7", "dash": "dash", "width": 1},
        },
        {
            "type": "line",
            "x0": plot_df["dIF"].min() * 1.1,
            "x1": plot_df["dIF"].max() * 1.1,
            "y0": -np.log10(sig_threshold),
            "y1": -np.log10(sig_threshold),
            "line": {"color": "#bdc3c7", "dash": "dash", "width": 1},
        },
    ]

    layout = {
        "title": "Isoform Switch Volcano Plot",
        "xaxis": {"title": "dIF (delta Isoform Fraction)"},
        "yaxis": {"title": "-log10(q-value)"},
        "shapes": shapes,
        "margin": {"l": 60, "b": 60, "t": 50, "r": 30},
    }

    return {"data": [trace], "layout": layout}


def _build_switch_type_bar(df: pd.DataFrame) -> dict | None:
    """Build a bar chart of switch consequence types (if available)."""
    if "switch_consequence" not in df.columns:
        return None

    sig_df = df[df.get("qvalue", pd.Series(dtype=float)) < 0.05]
    if sig_df.empty or sig_df["switch_consequence"].isna().all():
        return None

    counts = (
        sig_df["switch_consequence"]
        .dropna()
        .value_counts()
        .head(15)
    )
    if counts.empty:
        return None

    trace = {
        "type": "bar",
        "x": counts.values.tolist(),
        "y": counts.index.tolist(),
        "orientation": "h",
        "marker": {"color": "#059696"},
        "hovertemplate": "%{y}: %{x} events<extra></extra>",
    }

    layout = {
        "title": "Switch Consequence Types (FDR < 0.05)",
        "xaxis": {"title": "Count"},
        "yaxis": {"title": "", "autorange": "reversed"},
        "margin": {"l": 200, "b": 50, "t": 50, "r": 30},
    }

    return {"data": [trace], "layout": layout}
