"""
Plotly JSON serializers for ImpulseDE2 Time Series results.
=============================================================
Returns plain ``dict`` s that are JSON-serializable and directly
consumable by Plotly.js on the frontend (``Plotly.newPlot``).
Stored in ``AnalysisJob.result_payload``.
"""

import numpy as np
import pandas as pd


# Colour palette for top-gene trajectory lines
_PALETTE = ["#059696", "#e74c3c", "#3498db", "#f39c12", "#8e44ad"]


def build_timeseries_payload(
    result_df: pd.DataFrame,
    counts_df: pd.DataFrame,
    annotation_df: pd.DataFrame,
    *,
    time_unit: str = "hours",
    fdr_threshold: float = 0.05,
    top_n_table: int = 100,
    top_n_plot: int = 5,
) -> dict:
    """
    Serialize ImpulseDE2 results into the standard result_payload format.

    Parameters
    ----------
    result_df : pd.DataFrame
        ImpulseDE2 output with at least Gene, p-value, and padj columns.
    counts_df : pd.DataFrame
        Normalized count matrix (genes x samples).
    annotation_df : pd.DataFrame
        Sample annotation with columns: Sample, Condition, Time, Batch.
    time_unit : str
        Label for the x-axis (minutes, hours, days, weeks).
    fdr_threshold : float
        Significance cutoff for the summary count.
    top_n_table : int
        Number of top DEGs to include in table_data.
    top_n_plot : int
        Number of top genes to include in the trajectory plot.

    Returns
    -------
    dict
        ``{"summary_text": str, "table_data": list[dict], "plot_data": dict}``
    """
    # Normalize column names from ImpulseDE2 output
    result_df = _normalize_columns(result_df)

    # Sort by adjusted p-value
    result_df = result_df.sort_values("padj", na_position="last")

    # Summary
    sig_count = int((result_df["padj"] < fdr_threshold).sum())
    total = len(result_df)
    summary_text = (
        f"Found {sig_count} significantly time-dependent gene"
        f"{'s' if sig_count != 1 else ''} at FDR < {fdr_threshold} "
        f"(out of {total} tested)."
    )

    # Table data: top N by padj
    top_df = result_df.head(top_n_table)
    table_data = []
    for _, row in top_df.iterrows():
        table_data.append({
            "Gene": str(row.get("Gene", row.name)),
            "p-value": _safe_float(row.get("pvalue")),
            "padj": _safe_float(row.get("padj")),
        })

    # Plot data: trajectory line chart for top N genes
    trajectory_plot = _build_trajectory_plot(
        result_df=result_df,
        counts_df=counts_df,
        annotation_df=annotation_df,
        time_unit=time_unit,
        top_n=top_n_plot,
    )

    return {
        "summary_text": summary_text,
        "table_data": table_data,
        "plot_data": {
            "trajectory": trajectory_plot,
        },
    }


def _build_trajectory_plot(
    result_df: pd.DataFrame,
    counts_df: pd.DataFrame,
    annotation_df: pd.DataFrame,
    time_unit: str,
    top_n: int,
) -> dict:
    """
    Build a Plotly line chart: Time vs Mean Expression for top genes.

    Each gene gets a separate trace.  Points are the mean expression
    across replicates at each timepoint, with error bars showing SEM.
    """
    top_genes = result_df.head(top_n).index.tolist()
    if not top_genes:
        return _empty_trajectory(time_unit)

    # Build a tidy long-form table: gene, time, expression
    sample_time = annotation_df.set_index("Sample")["Time"].to_dict()

    traces = []
    for i, gene in enumerate(top_genes):
        if gene not in counts_df.index:
            continue

        expr = counts_df.loc[gene]
        records = []
        for sample, value in expr.items():
            if sample in sample_time:
                records.append({
                    "time": sample_time[sample],
                    "expr": value,
                })

        if not records:
            continue

        gene_df = pd.DataFrame(records)
        grouped = gene_df.groupby("time")["expr"].agg(["mean", "sem", "count"])
        grouped = grouped.sort_index()
        grouped["sem"] = grouped["sem"].fillna(0)

        # Gene label (use the gene name, possibly from result_df)
        gene_label = str(result_df.loc[gene].get("Gene", gene)) if "Gene" in result_df.columns else str(gene)

        padj_val = result_df.loc[gene].get("padj")
        padj_str = f"{padj_val:.2e}" if padj_val is not None and not np.isnan(padj_val) else "N/A"

        color = _PALETTE[i % len(_PALETTE)]

        trace = {
            "type": "scatter",
            "mode": "lines+markers",
            "name": f"{gene_label} (padj={padj_str})",
            "x": grouped.index.tolist(),
            "y": grouped["mean"].tolist(),
            "error_y": {
                "type": "data",
                "array": grouped["sem"].tolist(),
                "visible": True,
                "thickness": 1.5,
            },
            "line": {"color": color, "width": 2},
            "marker": {"size": 7, "color": color},
            "hovertemplate": (
                f"<b>{gene_label}</b><br>"
                f"Time: %{{x}} {time_unit}<br>"
                "Mean Expr: %{y:.1f}<br>"
                f"padj: {padj_str}"
                "<extra></extra>"
            ),
        }
        traces.append(trace)

    if not traces:
        return _empty_trajectory(time_unit)

    layout = {
        "title": f"Top {len(traces)} Time-Dependent Gene Trajectories",
        "xaxis": {
            "title": f"Time ({time_unit.capitalize()})",
            "tickmode": "auto",
        },
        "yaxis": {"title": "Mean Normalized Expression"},
        "legend": {
            "orientation": "h",
            "y": -0.2,
            "x": 0.5,
            "xanchor": "center",
        },
        "margin": {"l": 70, "b": 80, "t": 50, "r": 30},
        "hovermode": "x unified",
    }

    return {"data": traces, "layout": layout}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map ImpulseDE2 output column names to a standard set."""
    col_map = {}
    for col in df.columns:
        low = col.lower().replace(" ", "_").replace("-", "_")
        if low in ("gene", "geneid", "gene_id"):
            col_map[col] = "Gene"
        elif low in ("pvalue", "p_value", "p.value"):
            col_map[col] = "pvalue"
        elif low in ("padj", "p_adj", "adjusted_p_value", "p.adj"):
            col_map[col] = "padj"

    df = df.rename(columns=col_map)

    # If Gene is a column rather than the index, set it as index
    if "Gene" in df.columns and not df.index.name:
        df = df.set_index("Gene", drop=False)

    return df


def _safe_float(val) -> float | None:
    """Convert to float, returning None for NaN."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if np.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _empty_trajectory(time_unit: str) -> dict:
    """Return a placeholder plot when no significant genes exist."""
    return {
        "data": [{
            "type": "scatter",
            "mode": "lines+markers",
            "x": [],
            "y": [],
        }],
        "layout": {
            "title": "Gene Trajectories - No significant genes found",
            "xaxis": {"title": f"Time ({time_unit.capitalize()})"},
            "yaxis": {"title": "Mean Normalized Expression"},
            "annotations": [{
                "text": "No significant time-dependent genes to display",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5,
                "showarrow": False,
                "font": {"size": 14, "color": "#888"},
            }],
        },
    }
