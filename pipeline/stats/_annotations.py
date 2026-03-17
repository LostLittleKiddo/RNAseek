"""Automated Annotation Bridge — MyGene.info REST API integration.

Enriches DEG tables with plain-English gene descriptions and known disease
associations by querying the MyGene.info public API.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

_MYGENE_URL = "https://mygene.info/v3/query"
_BATCH_SIZE = 500
_TIMEOUT = 30  # seconds per request
_MAX_RETRIES = 3
_RETRY_BACKOFF = 2  # seconds, doubles each attempt


def annotate_deg_table(deg_df, gene_id_col="gene_id"):
    """Append gene descriptions and disease associations to a DEG DataFrame.

    Parameters
    ----------
    deg_df : pandas.DataFrame
        Differential expression results with a gene identifier column.
    gene_id_col : str
        Name of the column containing gene identifiers (Ensembl IDs or symbols).

    Returns
    -------
    pandas.DataFrame
        The input DataFrame with ``gene_description`` and ``disease_association``
        columns appended.  Original rows are never dropped.
    """
    import pandas as pd

    if gene_id_col not in deg_df.columns:
        logger.warning("Column '%s' not found — skipping annotation.", gene_id_col)
        deg_df["gene_description"] = ""
        deg_df["disease_association"] = ""
        return deg_df

    gene_ids = deg_df[gene_id_col].dropna().unique().tolist()
    if not gene_ids:
        deg_df["gene_description"] = ""
        deg_df["disease_association"] = ""
        return deg_df

    annotation_map = _fetch_annotations(gene_ids)

    deg_df["gene_description"] = (
        deg_df[gene_id_col]
        .map(lambda gid: annotation_map.get(str(gid), {}).get("description", ""))
    )
    deg_df["disease_association"] = (
        deg_df[gene_id_col]
        .map(lambda gid: annotation_map.get(str(gid), {}).get("diseases", ""))
    )

    return deg_df


def _fetch_annotations(gene_ids):
    """Query MyGene.info in batches and return {gene_id: {description, diseases}}.

    Uses POST /v3/query with ``scopes=symbol,ensembl.gene,entrezgene``
    to accommodate multiple ID formats.
    """
    results = {}

    for start in range(0, len(gene_ids), _BATCH_SIZE):
        batch = gene_ids[start : start + _BATCH_SIZE]
        batch_str = ",".join(str(g) for g in batch)

        data = _post_with_retry(batch_str)
        if data is None:
            continue

        for hit in data:
            if isinstance(hit, dict) and not hit.get("notfound"):
                query_id = str(hit.get("query", ""))
                description = _extract_description(hit)
                diseases = _extract_diseases(hit)
                results[query_id] = {
                    "description": description,
                    "diseases": diseases,
                }

    return results


def _post_with_retry(query_str):
    """POST to MyGene.info with exponential backoff retry."""
    payload = {
        "q": query_str,
        "scopes": "symbol,ensembl.gene,entrezgene",
        "fields": "name,summary,disease",
        "species": "human",
    }

    backoff = _RETRY_BACKOFF
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(
                _MYGENE_URL,
                data=payload,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp.json()

            if resp.status_code == 429:
                logger.warning(
                    "MyGene.info rate-limited (attempt %d/%d) — retrying in %ds",
                    attempt, _MAX_RETRIES, backoff,
                )
                time.sleep(backoff)
                backoff *= 2
                continue

            logger.warning(
                "MyGene.info returned HTTP %d (attempt %d/%d): %s",
                resp.status_code, attempt, _MAX_RETRIES,
                resp.text[:200],
            )

        except requests.exceptions.Timeout:
            logger.warning(
                "MyGene.info timeout (attempt %d/%d) — retrying in %ds",
                attempt, _MAX_RETRIES, backoff,
            )
            time.sleep(backoff)
            backoff *= 2

        except requests.exceptions.RequestException as exc:
            logger.warning(
                "MyGene.info request failed (attempt %d/%d): %s",
                attempt, _MAX_RETRIES, exc,
            )
            time.sleep(backoff)
            backoff *= 2

    logger.error(
        "MyGene.info: exhausted %d retries — annotation will be incomplete.",
        _MAX_RETRIES,
    )
    return None


def _extract_description(hit):
    """Extract a plain-English gene description from a MyGene.info hit."""
    summary = hit.get("summary", "")
    if summary:
        return str(summary)
    name = hit.get("name", "")
    return str(name)


def _extract_diseases(hit):
    """Extract disease associations as a semicolon-separated string."""
    disease_raw = hit.get("disease", [])
    if not disease_raw:
        return ""

    if isinstance(disease_raw, dict):
        disease_raw = [disease_raw]

    names = []
    for entry in disease_raw:
        if isinstance(entry, dict):
            dname = entry.get("name") or entry.get("disease_name", "")
            if dname and dname not in names:
                names.append(str(dname))

    return "; ".join(names)
