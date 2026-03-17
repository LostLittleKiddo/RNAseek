"""
Plotly JSON serializers for WGCNA & Pathway Enrichment results.
================================================================
All functions return plain ``dict`` s that are JSON-serializable and
directly consumable by Plotly.js on the frontend (``Plotly.newPlot``).
These dicts are stored in ``AnalysisJob.result_payload["plot_data"]``.
"""

import numpy as np
import pandas as pd


def build_module_trait_heatmap(
    cor_df: pd.DataFrame,
    pval_df: pd.DataFrame,
) -> dict:
    """
    Module-Trait Heatmap.

    Rows   = module eigengenes (colour-labelled gene networks).
    Cols   = experimental traits / conditions.
    Colour = Pearson *r* (red = positive, blue = negative).
    Text   = *r* value with significance stars.
    """
    # Pretty-print module names: "MEblue" -> "blue"
    y_labels = [
        m[2:] if m.startswith("ME") else m for m in cor_df.index.tolist()
    ]
    x_labels = cor_df.columns.tolist()

    # Build annotation text: "0.72 ***"
    cor_vals = cor_df.values.astype(float)
    pval_vals = pval_df.values.astype(float)
    annotation_text = _significance_labels(cor_vals, pval_vals)

    trace = {
        "type": "heatmap",
        "z": cor_vals.tolist(),
        "x": x_labels,
        "y": y_labels,
        "text": annotation_text,
        "hovertemplate": (
            "Module: %{y}<br>"
            "Trait: %{x}<br>"
            "r = %{z:.3f}<br>"
            "%{text}<extra></extra>"
        ),
        "colorscale": [
            [0.0, "#2166ac"],   # strong negative -- blue
            [0.25, "#67a9cf"],
            [0.5, "#f7f7f7"],   # zero -- white
            [0.75, "#ef8a62"],
            [1.0, "#b2182b"],   # strong positive -- red
        ],
        "zmid": 0,
        "colorbar": {"title": "Correlation", "thickness": 15},
    }

    layout = {
        "title": "Module-Trait Relationships",
        "xaxis": {"title": "Trait", "tickangle": -45},
        "yaxis": {"title": "Module", "autorange": "reversed"},
        "margin": {"l": 100, "b": 120, "t": 50, "r": 30},
    }

    return {"data": [trace], "layout": layout}


def build_pathway_dotplot(
    enrichment_df: pd.DataFrame,
    *,
    max_terms: int = 20,
) -> dict:
    """
    Pathway Dot Plot (Bubble Chart).

    Y-axis  = pathway / GO term name (top *max_terms* by significance).
    X-axis  = Combined Score (Enrichr's integrated ranking metric).
    Dot size = number of overlapping genes (Overlap_count).
    Colour  = -log10(adjusted p-value) -- darker = more significant.
    """
    if enrichment_df is None or enrichment_df.empty:
        return _empty_dotplot()

    df = (
        enrichment_df
        .loc[enrichment_df["Adjusted P-value"] < 0.05]
        .head(max_terms)
        .copy()
    )
    if df.empty:
        return _empty_dotplot()

    # -log10(adj p) for colour scale; clamp to avoid log(0).
    df["neg_log10_padj"] = -np.log10(df["Adjusted P-value"].clip(lower=1e-300))

    # Truncate long term names for readability.
    df["term_short"] = df["Term"].str[:60]

    # Bubble sizes: scale overlap counts to a readable pixel range.
    raw_sizes = df["Overlap_count"].values
    min_bubble, max_bubble = 8, 30
    if raw_sizes.max() == raw_sizes.min():
        scaled = np.full_like(raw_sizes, (min_bubble + max_bubble) / 2, dtype=float)
    else:
        scaled = min_bubble + (max_bubble - min_bubble) * (
            (raw_sizes - raw_sizes.min()) / (raw_sizes.max() - raw_sizes.min())
        )

    trace = {
        "type": "scatter",
        "mode": "markers",
        "x": df["Combined Score"].tolist(),
        "y": df["term_short"].tolist(),
        "marker": {
            "size": scaled.tolist(),
            "color": df["neg_log10_padj"].tolist(),
            "colorscale": "Viridis",
            "showscale": True,
            "colorbar": {"title": "-log10(adj p)", "thickness": 15},
            "line": {"width": 0.5, "color": "#333333"},
        },
        "text": [
            f"Genes: {oc}/{os_}<br>p-adj: {p:.2e}"
            for oc, os_, p in zip(
                df["Overlap_count"], df["Overlap_size"], df["Adjusted P-value"],
            )
        ],
        "hovertemplate": (
            "<b>%{y}</b><br>"
            "Combined Score: %{x:.1f}<br>"
            "%{text}<extra></extra>"
        ),
    }

    layout = {
        "title": "Pathway Enrichment - Top Hub-Gene Pathways",
        "xaxis": {"title": "Combined Score"},
        "yaxis": {
            "title": "",
            "autorange": "reversed",
            "automargin": True,
        },
        "margin": {"l": 280, "b": 60, "t": 50, "r": 30},
        "height": max(400, 28 * len(df)),
    }

    return {"data": [trace], "layout": layout}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _significance_labels(
    cor: np.ndarray,
    pval: np.ndarray,
) -> list[list[str]]:
    """Build a matrix of 'r (stars)' strings for heatmap hover/annotations."""
    rows: list[list[str]] = []
    for i in range(cor.shape[0]):
        row: list[str] = []
        for j in range(cor.shape[1]):
            r_val = cor[i, j]
            p_val = pval[i, j]
            if p_val < 0.001:
                stars = "***"
            elif p_val < 0.01:
                stars = "**"
            elif p_val < 0.05:
                stars = "*"
            else:
                stars = "ns"
            row.append(f"{r_val:.2f} ({stars})")
        rows.append(row)
    return rows


def _empty_dotplot() -> dict:
    """Return a placeholder plot when no significant terms exist."""
    return {
        "data": [{
            "type": "scatter",
            "mode": "markers",
            "x": [],
            "y": [],
            "marker": {"size": []},
        }],
        "layout": {
            "title": "Pathway Enrichment - No significant terms (adj. p < 0.05)",
            "xaxis": {"title": "Combined Score"},
            "yaxis": {"title": ""},
            "annotations": [{
                "text": "No significant pathways found for hub genes",
                "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5,
                "showarrow": False,
                "font": {"size": 14, "color": "#888"},
            }],
        },
    }
