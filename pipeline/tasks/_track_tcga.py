"""TCGA Disease Integration track.

Downloads RNA-Seq gene expression data and clinical metadata from the
NCI GDC API for a specified TCGA project, then hands off to the existing
matrix pipeline route (DESeq2).
"""

import json
import logging
import os
import time

import pandas as pd
import requests

from pipeline.tasks._helpers import _update_step
from pipeline.tasks._routes import _register_stage2_assets

logger = logging.getLogger(__name__)

# GDC API base URL
_GDC_API = "https://api.gdc.cancer.gov"
_GDC_FILES = f"{_GDC_API}/files"
_GDC_CASES = f"{_GDC_API}/cases"
_GDC_DATA = f"{_GDC_API}/data"

# Default page size for GDC queries
_GDC_PAGE_SIZE = 500

# Retry settings for GDC API calls
_MAX_RETRIES = 3
_RETRY_DELAY = 5


def _gdc_request(url, params=None, json_body=None, method="POST", stream=False):
    """Make a request to the GDC API with retry logic."""
    for attempt in range(_MAX_RETRIES):
        try:
            if method == "POST":
                resp = requests.post(
                    url, json=json_body, params=params,
                    timeout=120, stream=stream,
                )
            else:
                resp = requests.get(
                    url, params=params, timeout=120, stream=stream,
                )
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            if attempt < _MAX_RETRIES - 1:
                logger.warning(
                    "GDC API request failed (attempt %d/%d): %s",
                    attempt + 1, _MAX_RETRIES, exc,
                )
                time.sleep(_RETRY_DELAY * (attempt + 1))
            else:
                raise RuntimeError(
                    f"GDC API request failed after {_MAX_RETRIES} attempts: {exc}"
                ) from exc


def _download_tcga_expression(project_id, counts_dir):
    """Download STAR - Counts gene expression data from GDC for a TCGA project.

    Returns the path to a raw_counts.csv file (genes x samples) suitable
    for DESeq2.
    """
    # Query GDC for HTSeq/STAR count files in this project
    filters = {
        "op": "and",
        "content": [
            {
                "op": "=",
                "content": {
                    "field": "cases.project.project_id",
                    "value": project_id,
                },
            },
            {
                "op": "=",
                "content": {
                    "field": "data_category",
                    "value": "Transcriptome Profiling",
                },
            },
            {
                "op": "=",
                "content": {
                    "field": "data_type",
                    "value": "Gene Expression Quantification",
                },
            },
            {
                "op": "=",
                "content": {
                    "field": "analysis.workflow_type",
                    "value": "STAR - Counts",
                },
            },
            {
                "op": "=",
                "content": {
                    "field": "access",
                    "value": "open",
                },
            },
        ],
    }

    params = {
        "filters": json.dumps(filters),
        "fields": "file_id,file_name,cases.samples.sample_type,cases.submitter_id",
        "format": "JSON",
        "size": str(_GDC_PAGE_SIZE),
    }

    all_hits = []
    offset = 0
    while True:
        params["from"] = str(offset)
        resp = _gdc_request(_GDC_FILES, params=params, method="GET")
        data = resp.json()
        hits = data.get("data", {}).get("hits", [])
        if not hits:
            break
        all_hits.extend(hits)
        total = data.get("data", {}).get("pagination", {}).get("total", 0)
        offset += len(hits)
        if offset >= total:
            break

    if not all_hits:
        raise RuntimeError(
            f"No open-access STAR-Counts expression files found for {project_id}. "
            "Verify the project ID is correct."
        )

    logger.info("Found %d expression files for %s", len(all_hits), project_id)

    # Build file_id → sample_id mapping
    file_ids = []
    file_sample_map = {}
    for hit in all_hits:
        fid = hit["file_id"]
        file_ids.append(fid)
        # Extract submitter_id as sample name
        cases = hit.get("cases", [])
        if cases:
            sample_id = cases[0].get("submitter_id", fid[:12])
        else:
            sample_id = fid[:12]
        file_sample_map[fid] = sample_id

    # Download each file and extract the unstranded count column
    count_frames = []
    batch_size = 50
    for batch_start in range(0, len(file_ids), batch_size):
        batch = file_ids[batch_start:batch_start + batch_size]
        for fid in batch:
            sample_id = file_sample_map[fid]
            try:
                resp = _gdc_request(
                    f"{_GDC_DATA}/{fid}",
                    method="GET",
                    stream=False,
                )
                # GDC returns a TSV file for STAR-Counts
                lines = resp.text.strip().split("\n")
                genes = []
                counts = []
                for line in lines:
                    if line.startswith("N_") or line.startswith("#"):
                        continue  # Skip summary rows and comments
                    parts = line.split("\t")
                    if len(parts) >= 4:
                        gene_id = parts[0]  # Ensembl gene ID
                        gene_name = parts[1]
                        unstranded_count = parts[3]  # unstranded column
                        try:
                            count_val = int(float(unstranded_count))
                            # Use gene_name if available, else gene_id
                            genes.append(gene_name if gene_name else gene_id)
                            counts.append(count_val)
                        except (ValueError, IndexError):
                            continue

                if genes:
                    series = pd.Series(counts, index=genes, name=sample_id)
                    count_frames.append(series)
            except Exception as exc:
                logger.warning(
                    "Failed to download file %s for sample %s: %s",
                    fid, sample_id, exc,
                )
                continue

    if not count_frames:
        raise RuntimeError(
            f"Failed to download any expression data for {project_id}."
        )

    # Combine into a single DataFrame (genes x samples)
    counts_df = pd.concat(count_frames, axis=1)
    # Fill NaN with 0 (genes missing in some samples)
    counts_df = counts_df.fillna(0).astype(int)
    # Remove duplicate sample columns (keep first)
    counts_df = counts_df.loc[:, ~counts_df.columns.duplicated()]
    counts_df.index.name = "gene"

    counts_path = os.path.join(counts_dir, "raw_counts.csv")
    counts_df.to_csv(counts_path)
    logger.info(
        "TCGA expression matrix: %d genes x %d samples → %s",
        counts_df.shape[0], counts_df.shape[1], counts_path,
    )
    return counts_path


def _download_tcga_clinical(project_id, metadata_dir):
    """Download clinical metadata from the GDC API for a TCGA project.

    Returns the path to a metadata.csv with 'sample' column and clinical
    attributes suitable for DESeq2 design.
    """
    filters = {
        "op": "=",
        "content": {
            "field": "project.project_id",
            "value": project_id,
        },
    }

    fields = [
        "submitter_id",
        "demographic.gender",
        "demographic.vital_status",
        "diagnoses.tumor_stage",
        "diagnoses.primary_diagnosis",
        "diagnoses.age_at_diagnosis",
        "samples.sample_type",
    ]

    all_cases = []
    offset = 0
    while True:
        params = {
            "filters": json.dumps(filters),
            "fields": ",".join(fields),
            "format": "JSON",
            "size": str(_GDC_PAGE_SIZE),
            "from": str(offset),
        }
        resp = _gdc_request(_GDC_CASES, params=params, method="GET")
        data = resp.json()
        hits = data.get("data", {}).get("hits", [])
        if not hits:
            break
        all_cases.extend(hits)
        total = data.get("data", {}).get("pagination", {}).get("total", 0)
        offset += len(hits)
        if offset >= total:
            break

    if not all_cases:
        raise RuntimeError(
            f"No clinical data found for {project_id}."
        )

    rows = []
    for case in all_cases:
        row = {"sample": case.get("submitter_id", "")}

        demo = case.get("demographic", {}) or {}
        row["gender"] = demo.get("gender", "")
        row["vital_status"] = demo.get("vital_status", "")

        diagnoses = case.get("diagnoses", []) or []
        if diagnoses:
            diag = diagnoses[0]
            row["tumor_stage"] = diag.get("tumor_stage", "")
            row["primary_diagnosis"] = diag.get("primary_diagnosis", "")
            row["age_at_diagnosis"] = diag.get("age_at_diagnosis", "")

        samples = case.get("samples", []) or []
        sample_types = set()
        for s in samples:
            st = s.get("sample_type", "")
            if st:
                sample_types.add(st)
        row["sample_type"] = ";".join(sorted(sample_types)) if sample_types else ""

        # Create a 'condition' column from sample_type for DESeq2 contrast
        if sample_types:
            is_tumor = any("tumor" in t.lower() for t in sample_types)
            row["condition"] = "Tumor" if is_tumor else "Normal"
        else:
            row["condition"] = "Unknown"

        rows.append(row)

    meta_df = pd.DataFrame(rows)
    # Drop rows with empty sample IDs
    meta_df = meta_df[meta_df["sample"].str.strip().astype(bool)]

    meta_path = os.path.join(metadata_dir, "metadata.csv")
    meta_df.to_csv(meta_path, index=False)
    logger.info(
        "TCGA clinical metadata: %d cases → %s",
        len(meta_df), meta_path,
    )
    return meta_path


def _route_tcga(submission, job):
    """Route TCGA: Download expression + clinical data, then run DESeq2.

    Bridges into the existing matrix pipeline after downloading data.
    """
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    project_id = submission.tcga_project_id
    if not project_id:
        raise ValueError("No TCGA project ID specified.")

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    metadata_dir = os.path.join(work_dir, "metadata")
    os.makedirs(counts_dir, exist_ok=True)
    os.makedirs(metadata_dir, exist_ok=True)

    # Step 1: Download TCGA data
    _update_step(job, "tcga_download")

    counts_path = _download_tcga_expression(project_id, counts_dir)
    metadata_path = _download_tcga_clinical(project_id, metadata_dir)

    # Register downloaded files as FileAssets
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=counts_path,
        is_user_uploaded=False,
    )
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.METADATA_CSV,
        local_path=metadata_path,
        is_user_uploaded=False,
    )

    # Load the downloaded metadata into the submission's metadata_payload
    # so DESeq2 can use it for differential expression
    meta_df = pd.read_csv(metadata_path)
    samples = meta_df.to_dict(orient="records")

    # Build metadata payload with sensible defaults for TCGA
    submission.metadata_payload = {
        "samples": samples,
        "column_mapping": {
            "primary_group": "condition",
            "batch_effect": "",
            "additional_covariates": [],
        },
        "contrasts": [["Tumor", "Normal"]],
    }
    submission.metadata_mode = "upload"
    submission.save(
        update_fields=["metadata_payload", "metadata_mode"]
    )

    _update_step(job, "tcga_download", completed=True)

    # Step 2: Run DESeq2 (reuse the matrix path's Stage 2)
    _update_step(job, "deseq2")
    stats_result = run_stage2_stats(submission)
    _update_step(job, "deseq2", completed=True)

    _register_stage2_assets(submission, stats_result)

    return {"count_matrix": counts_path, "tcga_project_id": project_id, **stats_result}
