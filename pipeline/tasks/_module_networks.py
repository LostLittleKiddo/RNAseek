"""
Causal Network & STRING PPI Engine
====================================
Infers a directed causal network from normalized gene expression data,
identifies master-regulator hub genes, then enriches with protein-protein
interaction edges from the STRING database.

Pipeline steps:
    net_load_data -> net_correlation -> net_causal_inference ->
    net_string_ppi -> net_merge_graph -> net_serialize
"""

import logging
import os
import time

import networkx as nx
import numpy as np
import pandas as pd
import requests
from scipy import stats as scipy_stats

from pipeline.models import AnalysisJob
from pipeline.tasks._helpers import _emit_progress, _update_step

logger = logging.getLogger(__name__)

NETWORK_STEPS = [
    "net_load_data",
    "net_correlation",
    "net_causal_inference",
    "net_string_ppi",
    "net_merge_graph",
    "net_serialize",
]

# STRING API settings
STRING_API_BASE = "https://string-db.org/api/json"
STRING_NETWORK_URL = f"{STRING_API_BASE}/network"
STRING_MAX_GENES_PER_REQUEST = 200
STRING_REQUEST_TIMEOUT = 30  # seconds
STRING_RETRY_DELAY = 2  # seconds between retries
STRING_MAX_RETRIES = 3

# Causal network defaults
DEFAULT_TOP_GENES = 200       # Variance-filtered genes for network inference
DEFAULT_N_HUB_GENES = 25      # Hub genes to report
DEFAULT_CORRELATION_THRESHOLD = 0.6
DEFAULT_PCOR_THRESHOLD = 0.3  # Partial correlation threshold for edge retention
DEFAULT_STRING_SPECIES = 9606  # Homo sapiens


def execute_causal_network(
    job_id: str,
    session_id: str,
    matrix_path: str,
    *,
    tf_list: str | None = None,
    confidence: float = 0.7,
    n_hub_genes: int = DEFAULT_N_HUB_GENES,
    top_genes: int = DEFAULT_TOP_GENES,
    species: int = DEFAULT_STRING_SPECIES,
) -> dict:
    """End-to-end causal network inference + STRING PPI integration.

    Parameters
    ----------
    job_id : str
        UUID of the AnalysisJob tracking this run.
    session_id : str
        UUID of the owning Session.
    matrix_path : str
        Absolute path to normalized count matrix CSV (genes x samples).
    tf_list : str | None
        Newline-separated transcription factor gene names to prioritize.
    confidence : float
        STRING confidence threshold (0.15-1.0).
    n_hub_genes : int
        Number of top hub genes to extract.
    top_genes : int
        Number of high-variance genes to include in network inference.
    species : int
        NCBI taxonomy ID for STRING queries (default: 9606 = human).

    Returns
    -------
    dict
        Result payload with network_graph, hub_genes, summary, and plot_data.
    """
    job = AnalysisJob.objects.get(job_id=job_id, session_id=session_id)

    # Initialise step tracker.
    job.step_progress = {
        "pipeline_steps": list(NETWORK_STEPS),
        "current_step": None,
        "completed_steps": [],
        "failed_step": None,
    }
    job.save(update_fields=["step_progress"])
    _emit_progress(job)

    # Parse TF list
    tf_genes = set()
    if tf_list and tf_list.strip():
        tf_genes = {g.strip().upper() for g in tf_list.strip().split("\n") if g.strip()}

    # Clamp confidence
    confidence = max(0.15, min(1.0, confidence))

    # ------------------------------------------------------------------
    # Step 1 — Load & validate input data
    # ------------------------------------------------------------------
    _update_step(job, "net_load_data")

    counts_df = _load_expression_matrix(matrix_path)
    logger.info(
        "Network module: %d genes x %d samples loaded from %s",
        counts_df.shape[0], counts_df.shape[1], os.path.basename(matrix_path),
    )

    # Filter to top high-variance genes
    gene_var = counts_df.var(axis=1)
    n_select = min(top_genes, len(gene_var))
    top_gene_names = gene_var.nlargest(n_select).index.tolist()
    expr_df = counts_df.loc[top_gene_names]

    # If TFs specified, ensure they're included
    if tf_genes:
        available_tfs = tf_genes & set(counts_df.index.str.upper())
        missing_tfs = tf_genes - set(counts_df.index.str.upper())
        if missing_tfs:
            logger.warning("TFs not found in expression data: %s", missing_tfs)
        # Add available TFs not already in the top-variance set
        gene_upper_map = {g.upper(): g for g in counts_df.index}
        extra = [gene_upper_map[t] for t in available_tfs
                 if gene_upper_map[t] not in expr_df.index]
        if extra:
            expr_df = pd.concat([expr_df, counts_df.loc[extra]])

    logger.info("Network inference on %d genes", expr_df.shape[0])
    _update_step(job, "net_load_data", completed=True)

    # ------------------------------------------------------------------
    # Step 2 — Compute correlation matrix
    # ------------------------------------------------------------------
    _update_step(job, "net_correlation")

    corr_matrix = expr_df.T.corr(method="spearman")
    logger.info("Spearman correlation matrix computed: %dx%d", *corr_matrix.shape)

    _update_step(job, "net_correlation", completed=True)

    # ------------------------------------------------------------------
    # Step 3 — Causal network inference (partial correlation + directionality)
    # ------------------------------------------------------------------
    _update_step(job, "net_causal_inference")

    causal_graph = _infer_causal_network(
        expr_df, corr_matrix, tf_genes,
        pcor_threshold=DEFAULT_PCOR_THRESHOLD,
        corr_threshold=DEFAULT_CORRELATION_THRESHOLD,
    )

    # Identify hub genes by out-degree + betweenness centrality
    hub_genes = _identify_hub_genes(causal_graph, n_hub_genes, tf_genes)
    logger.info(
        "Causal network: %d nodes, %d edges, %d hub genes",
        causal_graph.number_of_nodes(),
        causal_graph.number_of_edges(),
        len(hub_genes),
    )

    _update_step(job, "net_causal_inference", completed=True)

    # ------------------------------------------------------------------
    # Step 4 — Query STRING PPI database
    # ------------------------------------------------------------------
    _update_step(job, "net_string_ppi")

    hub_gene_names = [h["gene"] for h in hub_genes]
    string_edges = _query_string_ppi(
        hub_gene_names,
        species=species,
        required_score=int(confidence * 1000),
    )
    logger.info("STRING PPI: %d interactions retrieved", len(string_edges))

    _update_step(job, "net_string_ppi", completed=True)

    # ------------------------------------------------------------------
    # Step 5 — Merge causal + PPI edges into unified graph
    # ------------------------------------------------------------------
    _update_step(job, "net_merge_graph")

    merged = _merge_graphs(causal_graph, string_edges, hub_gene_names)

    _update_step(job, "net_merge_graph", completed=True)

    # ------------------------------------------------------------------
    # Step 6 — Serialize to JSON payload
    # ------------------------------------------------------------------
    _update_step(job, "net_serialize")

    result = _build_result_payload(
        merged, hub_genes, causal_graph, string_edges, tf_genes,
    )

    _update_step(job, "net_serialize", completed=True)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_expression_matrix(matrix_path: str) -> pd.DataFrame:
    """Load a genes x samples expression matrix from CSV."""
    if not os.path.isfile(matrix_path):
        raise FileNotFoundError(f"Expression matrix not found: {matrix_path}")

    df = pd.read_csv(matrix_path, index_col=0)
    if df.empty:
        raise ValueError("Expression matrix is empty.")
    # Ensure numeric
    df = df.apply(pd.to_numeric, errors="coerce").dropna(how="all")
    if df.shape[0] < 10:
        raise ValueError(
            f"Too few genes ({df.shape[0]}) after filtering. "
            "Need at least 10 genes for network inference."
        )
    if df.shape[1] < 3:
        raise ValueError(
            f"Too few samples ({df.shape[1]}). "
            "Need at least 3 samples for correlation analysis."
        )
    return df


def _infer_causal_network(
    expr_df: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    tf_genes: set,
    *,
    pcor_threshold: float = DEFAULT_PCOR_THRESHOLD,
    corr_threshold: float = DEFAULT_CORRELATION_THRESHOLD,
) -> nx.DiGraph:
    """Infer a directed causal network using partial correlation and
    variance-based directionality scoring.

    Approach:
    1. Filter edges by absolute Spearman correlation > threshold.
    2. Compute first-order partial correlations for remaining edges.
    3. Assign directionality: gene with higher expression variance is
       considered the "regulator" (upstream). TFs get a directionality bonus.
    4. Build a directed graph with edge weights.
    """
    genes = expr_df.index.tolist()
    n_genes = len(genes)
    G = nx.DiGraph()

    # Pre-compute gene variance for directionality scoring
    gene_var = expr_df.var(axis=1)
    gene_var_norm = (gene_var - gene_var.min()) / (gene_var.max() - gene_var.min() + 1e-10)

    # Pre-compute the precision matrix for partial correlations
    # (more efficient than computing pairwise)
    try:
        corr_np = corr_matrix.values
        # Regularise to ensure invertibility
        corr_reg = corr_np + np.eye(n_genes) * 0.01
        precision = np.linalg.inv(corr_reg)
        # Partial correlation from precision matrix
        diag = np.sqrt(np.diag(precision))
        pcor_matrix = -precision / np.outer(diag, diag)
        np.fill_diagonal(pcor_matrix, 1.0)
    except np.linalg.LinAlgError:
        logger.warning("Precision matrix inversion failed; falling back to correlation only")
        pcor_matrix = corr_matrix.values
        pcor_threshold = corr_threshold

    # Build edges
    edges = []
    for i in range(n_genes):
        for j in range(i + 1, n_genes):
            abs_corr = abs(corr_matrix.iloc[i, j])
            if abs_corr < corr_threshold:
                continue

            abs_pcor = abs(pcor_matrix[i, j])
            if abs_pcor < pcor_threshold:
                continue

            gene_a = genes[i]
            gene_b = genes[j]

            # Directionality scoring: higher variance = more likely regulator
            score_a = gene_var_norm[gene_a]
            score_b = gene_var_norm[gene_b]

            # TF bonus: known TFs are more likely regulators
            if tf_genes:
                if gene_a.upper() in tf_genes:
                    score_a += 0.3
                if gene_b.upper() in tf_genes:
                    score_b += 0.3

            weight = float(abs_pcor)
            corr_sign = "positive" if corr_matrix.iloc[i, j] > 0 else "negative"

            if score_a >= score_b:
                edges.append((gene_a, gene_b, weight, corr_sign))
            else:
                edges.append((gene_b, gene_a, weight, corr_sign))

    # Add nodes and edges to graph
    for gene in genes:
        G.add_node(gene, variance=float(gene_var[gene]),
                   is_tf=gene.upper() in tf_genes)

    for source, target, weight, sign in edges:
        G.add_edge(source, target, weight=weight, sign=sign, edge_type="causal")

    return G


def _identify_hub_genes(G: nx.DiGraph, n_hub: int, tf_genes: set) -> list[dict]:
    """Identify top hub genes by composite score of out-degree,
    betweenness centrality, and TF status."""
    if G.number_of_nodes() == 0:
        return []

    out_deg = dict(G.out_degree())
    betweenness = nx.betweenness_centrality(G)

    # Normalise scores
    max_deg = max(out_deg.values()) if out_deg else 1
    max_bet = max(betweenness.values()) if betweenness else 1

    scores = {}
    for node in G.nodes():
        deg_score = out_deg.get(node, 0) / max(max_deg, 1)
        bet_score = betweenness.get(node, 0) / max(max_bet, 1e-10)
        tf_bonus = 0.15 if node.upper() in tf_genes else 0.0
        scores[node] = 0.5 * deg_score + 0.35 * bet_score + tf_bonus

    sorted_genes = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    hub_list = []
    for gene, score in sorted_genes[:n_hub]:
        hub_list.append({
            "gene": gene,
            "hub_score": round(score, 4),
            "out_degree": out_deg.get(gene, 0),
            "in_degree": G.in_degree(gene),
            "betweenness": round(betweenness.get(gene, 0), 4),
            "is_tf": gene.upper() in tf_genes,
        })

    return hub_list


def _query_string_ppi(
    gene_names: list[str],
    *,
    species: int = DEFAULT_STRING_SPECIES,
    required_score: int = 700,
) -> list[dict]:
    """Query the STRING database API for PPI interactions among gene_names.

    Handles rate limiting with retries and batches large gene lists.
    """
    if not gene_names:
        return []

    all_edges = []
    # Batch if necessary
    for start in range(0, len(gene_names), STRING_MAX_GENES_PER_REQUEST):
        batch = gene_names[start:start + STRING_MAX_GENES_PER_REQUEST]
        identifiers = "%0d".join(batch)

        params = {
            "identifiers": identifiers,
            "species": species,
            "required_score": required_score,
            "caller_identity": "rnaseek_pipeline",
        }

        for attempt in range(STRING_MAX_RETRIES):
            try:
                resp = requests.get(
                    STRING_NETWORK_URL,
                    params=params,
                    timeout=STRING_REQUEST_TIMEOUT,
                )
                if resp.status_code == 429:
                    # Rate limited — back off
                    wait = STRING_RETRY_DELAY * (attempt + 1)
                    logger.warning("STRING API rate limited; retrying in %ds", wait)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                for edge in data:
                    all_edges.append({
                        "source": edge.get("preferredName_A", edge.get("stringId_A", "")),
                        "target": edge.get("preferredName_B", edge.get("stringId_B", "")),
                        "score": edge.get("score", 0),
                        "edge_type": "string_ppi",
                    })
                break  # success
            except requests.exceptions.Timeout:
                logger.warning(
                    "STRING API timeout (attempt %d/%d)",
                    attempt + 1, STRING_MAX_RETRIES,
                )
                if attempt < STRING_MAX_RETRIES - 1:
                    time.sleep(STRING_RETRY_DELAY)
                else:
                    logger.error("STRING API timed out after %d retries", STRING_MAX_RETRIES)
            except requests.exceptions.RequestException as exc:
                logger.error("STRING API error: %s", exc)
                break  # Don't retry on non-transient errors

        # Small delay between batches to be polite
        if start + STRING_MAX_GENES_PER_REQUEST < len(gene_names):
            time.sleep(1)

    return all_edges


def _merge_graphs(
    causal_graph: nx.DiGraph,
    string_edges: list[dict],
    hub_gene_names: list[str],
) -> dict:
    """Merge the causal graph and STRING PPI edges into a unified
    JSON-serializable graph structure.

    Returns a dict with "nodes" and "edges" lists.
    """
    # Collect all nodes: hub genes + their causal neighbors
    hub_set = set(hub_gene_names)
    relevant_nodes = set(hub_gene_names)

    # Add causal neighbors of hub genes (1-hop)
    for hub in hub_gene_names:
        if hub in causal_graph:
            relevant_nodes.update(causal_graph.successors(hub))
            relevant_nodes.update(causal_graph.predecessors(hub))

    # Add STRING nodes
    for edge in string_edges:
        relevant_nodes.add(edge["source"])
        relevant_nodes.add(edge["target"])

    # Build node list
    nodes = []
    for node in sorted(relevant_nodes):
        node_data = causal_graph.nodes.get(node, {})
        nodes.append({
            "id": node,
            "is_hub": node in hub_set,
            "is_tf": node_data.get("is_tf", False),
            "variance": node_data.get("variance", 0),
        })

    # Build edge list — causal edges for relevant nodes
    edges = []
    seen = set()
    for u, v, data in causal_graph.edges(data=True):
        if u in relevant_nodes and v in relevant_nodes:
            key = (u, v, "causal")
            if key not in seen:
                seen.add(key)
                edges.append({
                    "source": u,
                    "target": v,
                    "weight": round(data.get("weight", 0), 4),
                    "sign": data.get("sign", "positive"),
                    "edge_type": "causal",
                })

    # Add STRING edges
    for edge in string_edges:
        key = (edge["source"], edge["target"], "string_ppi")
        rev_key = (edge["target"], edge["source"], "string_ppi")
        if key not in seen and rev_key not in seen:
            seen.add(key)
            edges.append({
                "source": edge["source"],
                "target": edge["target"],
                "weight": round(edge.get("score", 0), 4),
                "sign": "physical",
                "edge_type": "string_ppi",
            })

    return {"nodes": nodes, "edges": edges}


def _build_result_payload(
    merged: dict,
    hub_genes: list[dict],
    causal_graph: nx.DiGraph,
    string_edges: list[dict],
    tf_genes: set,
) -> dict:
    """Build the final result payload with network data, hub genes,
    summary stats, and Plotly-compatible plot data."""

    hub_gene_names = [h["gene"] for h in hub_genes]

    # Build the network visualization plot data
    plot_data = _build_network_plot(merged, hub_gene_names, tf_genes)

    # Summary text
    n_causal = sum(1 for e in merged["edges"] if e["edge_type"] == "causal")
    n_ppi = sum(1 for e in merged["edges"] if e["edge_type"] == "string_ppi")

    summary = (
        f"Causal network inferred with {causal_graph.number_of_nodes()} genes "
        f"and {causal_graph.number_of_edges()} directed edges. "
        f"Identified {len(hub_genes)} hub genes. "
        f"STRING PPI query returned {len(string_edges)} interactions. "
        f"Merged graph: {len(merged['nodes'])} nodes, "
        f"{n_causal} causal edges + {n_ppi} PPI edges."
    )

    # Hub gene table (HTML)
    table_html = (
        '<table class="md-example-table"><thead><tr>'
        "<th>Gene</th><th>Hub Score</th><th>Out-Degree</th>"
        "<th>In-Degree</th><th>Betweenness</th><th>TF</th>"
        "</tr></thead><tbody>"
    )
    for h in hub_genes:
        tf_mark = "Yes" if h["is_tf"] else ""
        table_html += (
            f'<tr><td><strong>{h["gene"]}</strong></td>'
            f'<td>{h["hub_score"]}</td>'
            f'<td>{h["out_degree"]}</td>'
            f'<td>{h["in_degree"]}</td>'
            f'<td>{h["betweenness"]}</td>'
            f"<td>{tf_mark}</td></tr>"
        )
    table_html += "</tbody></table>"

    return {
        "module": "NETWORKS",
        "summary": summary,
        "hub_genes": hub_gene_names,
        "hub_gene_details": hub_genes,
        "network_graph": merged,
        "plot_data": plot_data,
        "table_preview": table_html,
        "stats": {
            "total_nodes": len(merged["nodes"]),
            "causal_edges": n_causal,
            "ppi_edges": n_ppi,
            "hub_count": len(hub_genes),
            "string_confidence_used": None,  # filled by caller if needed
        },
    }


def _build_network_plot(
    merged: dict,
    hub_gene_names: list[str],
    tf_genes: set,
) -> dict:
    """Build Plotly-compatible scatter plot data for the network graph.

    Uses a spring layout from NetworkX for node positions, then renders:
    - Nodes as a scatter trace (sized by hub status, colored by type)
    - Causal edges as solid lines (blue)
    - STRING PPI edges as dashed lines (orange)
    """
    # Build a NetworkX graph for layout computation
    G = nx.Graph()
    for node in merged["nodes"]:
        G.add_node(node["id"])
    for edge in merged["edges"]:
        G.add_edge(edge["source"], edge["target"])

    if G.number_of_nodes() == 0:
        return {}

    # Compute layout
    pos = nx.spring_layout(G, k=1.5 / (G.number_of_nodes() ** 0.5 + 0.1),
                           iterations=50, seed=42)

    # Separate edge traces by type
    causal_edge_x, causal_edge_y = [], []
    ppi_edge_x, ppi_edge_y = [], []

    for edge in merged["edges"]:
        src = edge["source"]
        tgt = edge["target"]
        if src not in pos or tgt not in pos:
            continue

        x0, y0 = pos[src]
        x1, y1 = pos[tgt]

        if edge["edge_type"] == "causal":
            causal_edge_x.extend([x0, x1, None])
            causal_edge_y.extend([y0, y1, None])
        else:
            ppi_edge_x.extend([x0, x1, None])
            ppi_edge_y.extend([y0, y1, None])

    # Node trace
    node_x, node_y, node_text, node_color, node_size = [], [], [], [], []
    hub_set = set(hub_gene_names)

    for node_data in merged["nodes"]:
        nid = node_data["id"]
        if nid not in pos:
            continue
        x, y = pos[nid]
        node_x.append(x)
        node_y.append(y)
        node_text.append(nid)

        # Color: TF = red, hub = teal, regular = grey
        if node_data.get("is_tf"):
            node_color.append("#E63946")   # red for TFs
        elif nid in hub_set:
            node_color.append("#2EC4B6")   # teal for hubs
        else:
            node_color.append("#8D99AE")   # grey for others

        # Size: hub genes larger
        node_size.append(18 if nid in hub_set else 10)

    traces = []

    # Causal edges trace (solid blue lines)
    if causal_edge_x:
        traces.append({
            "x": causal_edge_x,
            "y": causal_edge_y,
            "mode": "lines",
            "type": "scatter",
            "line": {"width": 1.5, "color": "rgba(30, 58, 138, 0.5)"},
            "hoverinfo": "none",
            "name": "Causal Edges (data-driven)",
            "showlegend": True,
        })

    # PPI edges trace (dashed orange lines)
    if ppi_edge_x:
        traces.append({
            "x": ppi_edge_x,
            "y": ppi_edge_y,
            "mode": "lines",
            "type": "scatter",
            "line": {"width": 1.2, "color": "rgba(230, 126, 34, 0.5)", "dash": "dash"},
            "hoverinfo": "none",
            "name": "STRING PPI Edges",
            "showlegend": True,
        })

    # Node trace
    traces.append({
        "x": node_x,
        "y": node_y,
        "mode": "markers+text",
        "type": "scatter",
        "text": node_text,
        "textposition": "top center",
        "textfont": {"size": 9, "color": "#2B2D42"},
        "marker": {
            "size": node_size,
            "color": node_color,
            "line": {"width": 1, "color": "#2B2D42"},
        },
        "hoverinfo": "text",
        "hovertext": [
            f"<b>{t}</b><br>Hub: {'Yes' if t in hub_set else 'No'}"
            f"<br>TF: {'Yes' if t.upper() in tf_genes else 'No'}"
            for t in node_text
        ],
        "name": "Genes",
        "showlegend": True,
    })

    layout = {
        "title": "Causal Network & STRING PPI Graph",
        "showlegend": True,
        "legend": {"x": 0, "y": -0.15, "orientation": "h"},
        "hovermode": "closest",
        "xaxis": {"showgrid": False, "zeroline": False, "showticklabels": False},
        "yaxis": {"showgrid": False, "zeroline": False, "showticklabels": False},
        "margin": {"l": 20, "r": 20, "t": 50, "b": 60},
        "plot_bgcolor": "rgba(0,0,0,0)",
        "paper_bgcolor": "rgba(0,0,0,0)",
    }

    return {
        "network_graph": {"data": traces, "layout": layout},
    }
