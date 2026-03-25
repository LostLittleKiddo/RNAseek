"""
Time Series Analysis Engine (ImpulseDE2)
=========================================
Runs ImpulseDE2 via rpy2 to identify genes with significant temporal
expression changes.  Supports both simple longitudinal (single condition)
and case-control longitudinal (two conditions) designs.

Pipeline steps:
    ts_load_data -> ts_build_annotation -> ts_run_impulse -> ts_serialize
"""

import csv
import io
import logging

import numpy as np
import pandas as pd

from pipeline.models import AnalysisJob
from pipeline.stats._plots_timeseries import build_timeseries_payload
from pipeline.tasks._helpers import _emit_progress, _update_step

logger = logging.getLogger(__name__)

TIMESERIES_STEPS = [
    "ts_load_data",
    "ts_build_annotation",
    "ts_run_impulse",
    "ts_serialize",
]


def execute_timeseries(
    job_id: str,
    session_id: str,
    matrix_path: str,
    mapping_data: dict,
    *,
    time_unit: str = "hours",
) -> dict:
    """
    End-to-end ImpulseDE2 time-series analysis.

    Parameters
    ----------
    job_id : str
        UUID of the AnalysisJob tracking this run.
    session_id : str
        UUID of the owning Session.
    matrix_path : str
        Absolute path to the normalized count matrix CSV (genes x samples).
    mapping_data : dict
        Frontend payload with either:
          - ``{"mode": "csv", "content": "<csv text>"}``
          - ``{"mode": "manual", "rows": [{"Sample_ID": ..., "Timepoint": ..., "Condition": ...}, ...]}``
    time_unit : str
        Label for time axis (minutes, hours, days, weeks).

    Returns
    -------
    dict
        Result payload with summary_text, table_data, and plot_data.
    """
    job = AnalysisJob.objects.get(job_id=job_id, session_id=session_id)

    job.step_progress = {
        "pipeline_steps": list(TIMESERIES_STEPS),
        "current_step": None,
        "completed_steps": [],
        "failed_step": None,
    }
    job.save(update_fields=["step_progress"])
    _emit_progress(job)

    # ------------------------------------------------------------------
    # Step 1 — Load & validate the normalized count matrix
    # ------------------------------------------------------------------
    _update_step(job, "ts_load_data")

    counts_df = pd.read_csv(matrix_path, index_col=0)
    logger.info(
        "Loaded count matrix: %d genes x %d samples", *counts_df.shape,
    )

    _update_step(job, "ts_load_data", completed=True)

    # ------------------------------------------------------------------
    # Step 2 — Parse sample mapping and build ImpulseDE2 annotation
    # ------------------------------------------------------------------
    _update_step(job, "ts_build_annotation")

    sample_map = _parse_mapping(mapping_data)
    annotation_df = _build_annotation(sample_map, counts_df.columns.tolist())

    # Determine case-control vs simple longitudinal
    unique_conditions = annotation_df["Condition"].dropna().unique()
    is_case_ctrl = len(unique_conditions) > 1

    logger.info(
        "Annotation: %d samples, %d timepoints, %d condition(s) -> %s",
        len(annotation_df),
        annotation_df["Time"].nunique(),
        len(unique_conditions),
        "case-control" if is_case_ctrl else "simple longitudinal",
    )

    _update_step(job, "ts_build_annotation", completed=True)

    # ------------------------------------------------------------------
    # Step 3 — Run ImpulseDE2 via rpy2
    # ------------------------------------------------------------------
    _update_step(job, "ts_run_impulse")

    result_df = _run_impulse_de2(counts_df, annotation_df, is_case_ctrl)

    logger.info("ImpulseDE2 returned %d gene results.", len(result_df))

    _update_step(job, "ts_run_impulse", completed=True)

    # ------------------------------------------------------------------
    # Step 4 — Serialize results
    # ------------------------------------------------------------------
    _update_step(job, "ts_serialize")

    payload = build_timeseries_payload(
        result_df=result_df,
        counts_df=counts_df,
        annotation_df=annotation_df,
        time_unit=time_unit,
    )

    _update_step(job, "ts_serialize", completed=True)

    return payload


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _parse_mapping(mapping_data: dict) -> list[dict]:
    """Parse frontend mapping payload into a list of row dicts."""
    mode = mapping_data.get("mode", "manual")

    if mode == "csv":
        content = mapping_data.get("content", "")
        if not content or not isinstance(content, str):
            raise ValueError("CSV mapping content is empty or invalid.")
        reader = csv.DictReader(io.StringIO(content))
        rows = []
        for row in reader:
            sid = (
                row.get("Sample_ID")
                or row.get("sample_id")
                or row.get("Sample", "")
            )
            tp = row.get("Timepoint") or row.get("timepoint") or row.get("Time", "")
            cond = (
                row.get("Condition")
                or row.get("condition")
                or "Control"
            )
            if not sid or tp == "":
                continue
            rows.append({
                "Sample_ID": sid.strip(),
                "Timepoint": float(tp),
                "Condition": cond.strip(),
            })
        return rows

    # mode == "manual"
    rows = mapping_data.get("rows", [])
    parsed = []
    for r in rows:
        sid = str(r.get("Sample_ID", "")).strip()
        tp = r.get("Timepoint")
        cond = str(r.get("Condition", "Control")).strip() or "Control"
        if not sid or tp is None:
            continue
        parsed.append({
            "Sample_ID": sid,
            "Timepoint": float(tp),
            "Condition": cond,
        })
    return parsed


def _build_annotation(
    sample_map: list[dict],
    matrix_columns: list[str],
) -> pd.DataFrame:
    """Build the ImpulseDE2-compatible annotation DataFrame.

    ImpulseDE2 expects columns: Sample, Condition, Time, Batch.
    """
    if not sample_map:
        raise ValueError("Sample mapping is empty. Provide at least one row.")

    df = pd.DataFrame(sample_map)
    df.rename(columns={
        "Sample_ID": "Sample",
        "Timepoint": "Time",
    }, inplace=True)

    # Validate that mapped samples exist in the count matrix
    missing = set(df["Sample"]) - set(matrix_columns)
    if missing:
        raise ValueError(
            f"Samples not found in count matrix: {', '.join(sorted(missing))}. "
            f"Available: {', '.join(matrix_columns[:10])}"
            + ("..." if len(matrix_columns) > 10 else ""),
        )

    # Add Batch column (required by ImpulseDE2; default to 1 if not provided)
    if "Batch" not in df.columns:
        df["Batch"] = "1"

    # Enforce column order
    df = df[["Sample", "Condition", "Time", "Batch"]]
    return df


def _run_impulse_de2(
    counts_df: pd.DataFrame,
    annotation_df: pd.DataFrame,
    is_case_ctrl: bool,
) -> pd.DataFrame:
    """Call ImpulseDE2::runImpulseDE2 via rpy2 and return a results DataFrame."""
    from pipeline.stats._r_bridge import _ensure_rpy2, localconverter, _converter, ro

    _ensure_rpy2()

    with localconverter(_converter):
        # Subset count matrix to only annotated samples, in annotation order
        sample_order = annotation_df["Sample"].tolist()
        mat = counts_df[sample_order].copy()

        # ImpulseDE2 expects integer counts (round if normalized floats)
        mat = mat.round().astype(int)

        # Transfer to R
        ro.r.assign("matCountData", mat)
        ro.r.assign("dfAnnotation", annotation_df)

        # Ensure correct R types
        ro.r("""
            matCountData <- as.matrix(matCountData)
            dfAnnotation$Time <- as.numeric(dfAnnotation$Time)
            dfAnnotation$Condition <- as.character(dfAnnotation$Condition)
            dfAnnotation$Batch <- as.character(dfAnnotation$Batch)
        """)

        # Load ImpulseDE2 and run
        ro.r("""
            suppressPackageStartupMessages(library(ImpulseDE2))
        """)

        case_ctrl_str = "TRUE" if is_case_ctrl else "FALSE"
        ro.r(f"""
            objImpulseDE2 <- runImpulseDE2(
                matCountData   = matCountData,
                dfAnnotation   = dfAnnotation,
                boolCaseCtrl   = {case_ctrl_str},
                scaNProc       = 2,
                boolIdentifyTransients = TRUE
            )
            dfResults <- as.data.frame(objImpulseDE2$dfImpulseDE2Results)
        """)

        result_df = ro.r("dfResults")

    return result_df
