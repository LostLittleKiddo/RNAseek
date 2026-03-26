"""
RNA Editing Engine — REDItools2
================================
Detects RNA editing sites (A-to-I, C-to-U) from aligned BAM files using
REDItools2 and produces summary statistics, Plotly chart data, and a
filterable site table for the frontend.

Pipeline steps:
    rna_editing_prepare -> rna_editing_reditools -> rna_editing_filter -> rna_editing_plots
"""

import logging
import os
import tempfile

import pandas as pd

from pipeline.models import AnalysisJob
from pipeline.tasks._constants import _TOOL_THREADS
from pipeline.tasks._helpers import _emit_progress, _q, _run, _update_step

logger = logging.getLogger(__name__)

RNA_EDITING_STEPS = [
    "rna_editing_prepare",
    "rna_editing_reditools",
    "rna_editing_filter",
    "rna_editing_plots",
]

# Minimum read coverage at a site to consider it reliable.
MIN_COVERAGE = 10

# Maximum sites to return in the frontend preview table.
MAX_TABLE_ROWS = 100

# Substitution types of primary biological interest.
EDITS_OF_INTEREST = {"AG", "TC"}  # A-to-I (A→G on cDNA) and C-to-U (C→T)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute_rna_editing(
    job_id: str,
    session_id: str,
    bam_paths: list[str],
    genome_fasta: str,
    *,
    bed_path: str | None = None,
    whole_transcriptome: bool = False,
) -> dict:
    """Run REDItools2 on one or more BAM files and return a result payload.

    Parameters
    ----------
    job_id : str
        UUID of the AnalysisJob tracking this run.
    session_id : str
        UUID of the owning Session (for tenant isolation).
    bam_paths : list[str]
        Absolute paths to sorted, indexed BAM files produced by the core
        pipeline alignment step.
    genome_fasta : str
        Absolute path to the reference genome FASTA (must have a .fai index).
    bed_path : str | None
        Optional BED file restricting analysis to specific genomic regions.
        Ignored when *whole_transcriptome* is True.
    whole_transcriptome : bool
        When True, scan the entire transcriptome (no BED filter).

    Returns
    -------
    dict
        JSON-serialisable payload with ``summary``, ``plot_data``, and
        ``table_preview`` keys matching the frontend result renderer.
    """
    job = AnalysisJob.objects.get(job_id=job_id, session_id=session_id)

    # Initialise step tracker for the frontend progress bar.
    job.step_progress = {
        "pipeline_steps": list(RNA_EDITING_STEPS),
        "current_step": None,
        "completed_steps": [],
        "failed_step": None,
    }
    job.save(update_fields=["step_progress"])
    _emit_progress(job)

    work_dir = os.path.join(os.path.dirname(bam_paths[0]), "rna_editing")
    os.makedirs(work_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1 — Validate inputs
    # ------------------------------------------------------------------
    _update_step(job, "rna_editing_prepare")

    _validate_inputs(bam_paths, genome_fasta, bed_path, whole_transcriptome)
    _ensure_fasta_index(genome_fasta)

    _update_step(job, "rna_editing_prepare", completed=True)

    # ------------------------------------------------------------------
    # Step 2 — Run REDItools2 on each BAM
    # ------------------------------------------------------------------
    _update_step(job, "rna_editing_reditools")

    raw_tables: list[pd.DataFrame] = []
    for bam in bam_paths:
        output_tsv = os.path.join(work_dir, os.path.basename(bam) + ".reditools.tsv")
        _run_reditools2(
            bam_path=bam,
            genome_fasta=genome_fasta,
            output_path=output_tsv,
            bed_path=None if whole_transcriptome else bed_path,
        )
        if os.path.isfile(output_tsv):
            df = _parse_reditools_output(output_tsv)
            if not df.empty:
                raw_tables.append(df)

    if not raw_tables:
        raise RuntimeError(
            "REDItools2 produced no output. Check that the BAM files are "
            "aligned to the supplied reference genome."
        )

    combined = pd.concat(raw_tables, ignore_index=True)

    _update_step(job, "rna_editing_reditools", completed=True)

    # ------------------------------------------------------------------
    # Step 3 — Filter for biologically meaningful edits
    # ------------------------------------------------------------------
    _update_step(job, "rna_editing_filter")

    filtered = _filter_editing_sites(combined)

    logger.info(
        "RNA editing: %d raw sites → %d after filtering (cov >= %d, A→I / C→U)",
        len(combined), len(filtered), MIN_COVERAGE,
    )

    _update_step(job, "rna_editing_filter", completed=True)

    # ------------------------------------------------------------------
    # Step 4 — Build summary, chart data, and preview table
    # ------------------------------------------------------------------
    _update_step(job, "rna_editing_plots")

    summary_stats = _compute_summary(filtered)
    bar_chart = _build_substitution_bar_chart(combined)
    table_html = _build_table_preview(filtered)

    _update_step(job, "rna_editing_plots", completed=True)

    return {
        "summary": (
            f"Detected {summary_stats['total_sites']} high-confidence editing sites "
            f"(coverage ≥ {MIN_COVERAGE}). "
            f"Average editing frequency: {summary_stats['avg_editing_freq']:.3f}."
        ),
        "plot_data": {
            "substitution_types": bar_chart,
        },
        "table_preview": table_html,
        "editing_stats": summary_stats,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate_inputs(
    bam_paths: list[str],
    genome_fasta: str,
    bed_path: str | None,
    whole_transcriptome: bool,
) -> None:
    """Ensure all required input files exist on the NFS mount."""
    for bam in bam_paths:
        if not os.path.isfile(bam):
            raise FileNotFoundError(f"BAM file not found: {bam}")
    if not os.path.isfile(genome_fasta):
        raise FileNotFoundError(f"Reference FASTA not found: {genome_fasta}")
    if not whole_transcriptome and bed_path and not os.path.isfile(bed_path):
        raise FileNotFoundError(f"BED file not found: {bed_path}")


def _ensure_fasta_index(genome_fasta: str) -> None:
    """Create the .fai index if it doesn't already exist."""
    fai_path = genome_fasta + ".fai"
    if not os.path.isfile(fai_path):
        _run(f"samtools faidx {_q(genome_fasta)}")


def _run_reditools2(
    bam_path: str,
    genome_fasta: str,
    output_path: str,
    bed_path: str | None = None,
) -> None:
    """Execute the REDItools2 command-line tool on a single BAM file.

    REDItools2 arguments explained:
      -f  BAM_PATH          Input aligned BAM file (must be sorted and indexed).
      -r  GENOME_FASTA      Reference genome FASTA (must have .fai index).
      -o  OUTPUT_PATH        Path for the tab-separated output table.
      -t  THREADS            Number of parallel threads for multi-core execution.
      -q  10                 Minimum read quality (Phred) to include a base call.
      -bq 25                 Minimum base quality at the candidate editing site.
      -m  255                Maximum haplotypes/mapping distance (keep all).
      -s  2                  Strand-specific mode: 2 = infer from XS tag.
      -B  BED_PATH           Restrict analysis to regions in the BED file.
    """
    # Build the command piece by piece for clarity.
    cmd_parts = [
        "reditools2.py",
        f"-f {_q(bam_path)}",              # Input BAM
        f"-r {_q(genome_fasta)}",           # Reference genome FASTA
        f"-o {_q(output_path)}",            # Output table path
        f"-t {_TOOL_THREADS}",              # Parallel threads
        "-q 10",                            # Min read mapping quality (Phred >= 10)
        "-bq 25",                           # Min base quality at the editing site
        "-m 255",                           # Max mapping distance (no filter)
        "-s 2",                             # Strand: infer from alignment XS tag
    ]

    # Optionally restrict to target regions from a user-provided BED file.
    if bed_path:
        cmd_parts.append(f"-B {_q(bed_path)}")

    _run(" ".join(cmd_parts))


def _parse_reditools_output(tsv_path: str) -> pd.DataFrame:
    """Parse the standard REDItools2 tab-separated output into a DataFrame.

    REDItools2 output columns (0-indexed):
      0  Region      — chromosome / contig
      1  Position    — 1-based genomic coordinate
      2  Reference   — reference base (A/C/G/T)
      3  Strand      — strand (+/-)
      4  Coverage    — total read coverage at the site
      5  MeanQuality — average base quality of variant reads
      6  BaseCount   — comma-separated counts: [A, C, G, T]
      7  AllSubs     — all substitution types detected (e.g. "AG CT")
      8  Frequency   — editing frequency (variant reads / coverage)
    """
    expected_cols = [
        "Region", "Position", "Reference", "Strand",
        "Coverage", "MeanQuality", "BaseCount", "AllSubs", "Frequency",
    ]

    try:
        df = pd.read_csv(
            tsv_path, sep="\t", header=0,
            names=expected_cols, comment="#",
            dtype={"Region": str, "Position": int, "Coverage": int},
        )
    except Exception:
        logger.warning("Failed to parse REDItools2 output: %s", tsv_path)
        return pd.DataFrame()

    # Drop rows where key columns are missing.
    df.dropna(subset=["Region", "Position", "AllSubs", "Coverage"], inplace=True)

    # Ensure numeric types.
    df["Coverage"] = pd.to_numeric(df["Coverage"], errors="coerce").fillna(0).astype(int)
    df["Frequency"] = pd.to_numeric(df["Frequency"], errors="coerce").fillna(0.0)

    return df


def _filter_editing_sites(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to A-to-I and C-to-U edits with adequate coverage.

    A-to-I editing appears as A→G on the cDNA strand (substitution type "AG").
    C-to-U editing appears as C→T (substitution type "TC" or "CT").
    """
    if df.empty:
        return df

    # Coverage filter — require at least MIN_COVERAGE reads.
    mask_cov = df["Coverage"] >= MIN_COVERAGE

    # Substitution-type filter — keep only edits of interest.
    def _has_target_edit(all_subs: str) -> bool:
        """Check if the AllSubs field contains any edit of interest."""
        tokens = str(all_subs).strip().split()
        return any(t.upper() in EDITS_OF_INTEREST for t in tokens)

    mask_edit = df["AllSubs"].apply(_has_target_edit)

    return df.loc[mask_cov & mask_edit].copy().reset_index(drop=True)


def _compute_summary(filtered: pd.DataFrame) -> dict:
    """Compute summary statistics for the filtered editing sites."""
    total = len(filtered)
    avg_freq = float(filtered["Frequency"].mean()) if total > 0 else 0.0
    avg_cov = float(filtered["Coverage"].mean()) if total > 0 else 0.0

    # Count per substitution type.
    type_counts: dict[str, int] = {}
    for subs in filtered["AllSubs"]:
        for token in str(subs).strip().split():
            key = token.upper()
            if key in EDITS_OF_INTEREST:
                type_counts[key] = type_counts.get(key, 0) + 1

    return {
        "total_sites": total,
        "avg_editing_freq": round(avg_freq, 4),
        "avg_coverage": round(avg_cov, 1),
        "a_to_i_count": type_counts.get("AG", 0),
        "c_to_u_count": type_counts.get("TC", 0),
    }


def _build_substitution_bar_chart(df: pd.DataFrame) -> dict:
    """Build a Plotly bar chart payload showing the distribution of all
    substitution types across the unfiltered dataset.

    Returns a dict with ``data`` and ``layout`` keys ready for Plotly.newPlot().
    """
    if df.empty:
        return {"data": [], "layout": {}}

    # Count every observed substitution type.
    counts: dict[str, int] = {}
    for subs in df["AllSubs"]:
        for token in str(subs).strip().split():
            key = token.upper()
            if len(key) == 2 and key.isalpha():
                counts[key] = counts.get(key, 0) + 1

    if not counts:
        return {"data": [], "layout": {}}

    # Sort by count descending for readability.
    sorted_types = sorted(counts.keys(), key=lambda k: -counts[k])
    x_labels = [f"{t[0]}→{t[1]}" for t in sorted_types]
    y_values = [counts[t] for t in sorted_types]

    # Highlight A→I and C→U in a distinct colour.
    colours = [
        "#059a98" if t in EDITS_OF_INTEREST else "#94a3b8"
        for t in sorted_types
    ]

    return {
        "data": [
            {
                "type": "bar",
                "x": x_labels,
                "y": y_values,
                "marker": {"color": colours},
                "hovertemplate": "%{x}: %{y} sites<extra></extra>",
            }
        ],
        "layout": {
            "title": {"text": "RNA Editing — Substitution Type Distribution", "font": {"size": 14}},
            "xaxis": {"title": "Substitution Type"},
            "yaxis": {"title": "Number of Sites"},
            "paper_bgcolor": "transparent",
            "plot_bgcolor": "transparent",
            "font": {"family": "Inter, system-ui, sans-serif", "size": 12, "color": "#334155"},
            "margin": {"l": 55, "r": 20, "t": 40, "b": 60},
        },
    }


def _build_table_preview(filtered: pd.DataFrame) -> str:
    """Build an HTML table string for the top editing sites.

    Sorted by editing frequency descending, capped at MAX_TABLE_ROWS rows.
    """
    if filtered.empty:
        return "<p>No editing sites passed the quality filters.</p>"

    top = (
        filtered
        .sort_values("Frequency", ascending=False)
        .head(MAX_TABLE_ROWS)
    )

    rows_html = []
    for _, row in top.iterrows():
        rows_html.append(
            f"<tr>"
            f"<td>{_esc(str(row['Region']))}</td>"
            f"<td>{int(row['Position'])}</td>"
            f"<td>{_esc(str(row['Reference']))}</td>"
            f"<td>{_esc(str(row['AllSubs']))}</td>"
            f"<td>{int(row['Coverage'])}</td>"
            f"<td>{row['Frequency']:.3f}</td>"
            f"</tr>"
        )

    return (
        '<table class="md-example-table">'
        "<thead><tr>"
        "<th>Chr</th><th>Position</th><th>Ref</th>"
        "<th>Edit Type</th><th>Coverage</th><th>Frequency</th>"
        "</tr></thead><tbody>"
        + "\n".join(rows_html)
        + "</tbody></table>"
    )


def _esc(text: str) -> str:
    """Minimal HTML escaping for table cell values."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
