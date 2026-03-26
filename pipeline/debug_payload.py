"""Payload debugger for the /api/pipeline/core endpoint.

Usage as a standalone script (inside the rnaseek conda env):
    python debug_payload.py

This creates a Django middleware that intercepts and pretty-prints the JSON
payload hitting CorePipelineView before it reaches Celery/rpy2.

To activate temporarily, add to config/settings.py MIDDLEWARE list:
    'pipeline.debug_payload.PayloadDebugMiddleware',
(Remove it after debugging.)

Alternatively, run this file directly as a script to parse and validate
a JSON payload from stdin or from a file:
    echo '{"submission_id":"...","metadata_payload":{...}}' | python debug_payload.py
    python debug_payload.py payload.json
"""

import json
import logging
import sys

logger = logging.getLogger("rnaseek.payload_debug")


class PayloadDebugMiddleware:
    """Django middleware that logs the raw JSON payload for /api/pipeline/core."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/api/pipeline/core" and request.method == "POST":
            try:
                body = json.loads(request.body) if request.body else {}
                self._log_payload(body)
            except json.JSONDecodeError:
                logger.error("PAYLOAD DEBUG: Body is not valid JSON")
        return self.get_response(request)

    @staticmethod
    def _log_payload(body):
        sep = "=" * 70
        logger.info("\n%s\nPAYLOAD DEBUG — /api/pipeline/core\n%s", sep, sep)

        logger.info("  submission_id:   %s", body.get("submission_id"))
        logger.info("  input_data_type: %s", body.get("input_data_type"))
        logger.info("  assay_type:      %s", body.get("assay_type"))
        logger.info("  library_type:    %s", body.get("library_type"))
        logger.info("  reference_genome:%s", body.get("reference_genome"))
        logger.info("  metadata_mode:   %s", body.get("metadata_mode"))
        logger.info("  adjusted_pvalue: %s", body.get("adjusted_pvalue"))
        logger.info("  min_log2fc:      %s", body.get("min_log2fc"))
        logger.info("  max_log2fc:      %s", body.get("max_log2fc"))

        meta = body.get("metadata_payload", {})
        samples = meta.get("samples", [])
        mapping = meta.get("column_mapping", {})
        contrasts = meta.get("contrasts", [])

        logger.info("\n  column_mapping:")
        logger.info("    primary_group:          %s", mapping.get("primary_group"))
        logger.info("    batch_effect:           %s", mapping.get("batch_effect"))
        logger.info("    additional_covariates:  %s", mapping.get("additional_covariates"))
        logger.info("    covariates (legacy):    %s", mapping.get("covariates"))

        logger.info("\n  contrasts (%d):", len(contrasts))
        for i, pair in enumerate(contrasts):
            logger.info("    [%d] target=%s  vs  reference=%s", i, pair[0] if len(pair) > 0 else "?", pair[1] if len(pair) > 1 else "?")

        logger.info("\n  samples (%d rows):", len(samples))
        if samples:
            cols = list(samples[0].keys()) if isinstance(samples[0], dict) else []
            logger.info("    columns: %s", cols)
            for row in samples[:5]:
                logger.info("    %s", row)
            if len(samples) > 5:
                logger.info("    ... (%d more rows)", len(samples) - 5)

        # Validation warnings
        warnings = []
        if not mapping.get("primary_group"):
            warnings.append("MISSING primary_group in column_mapping")
        if mapping.get("covariates") and not mapping.get("additional_covariates"):
            warnings.append(
                "KEY MISMATCH: frontend sent 'covariates' but backend expects "
                "'additional_covariates'. The backend now accepts both, but "
                "the frontend should be updated."
            )
        if contrasts:
            primary = mapping.get("primary_group", "")
            if samples and isinstance(samples[0], dict):
                group_vals = set()
                for row in samples:
                    v = row.get(primary, "")
                    if v:
                        group_vals.add(v)
                for pair in contrasts:
                    for val in pair:
                        if val and val not in group_vals:
                            warnings.append(
                                "Contrast level '%s' not found in primary_group "
                                "values: %s" % (val, sorted(group_vals))
                            )

        if warnings:
            logger.warning("\n  ⚠ WARNINGS:")
            for w in warnings:
                logger.warning("    - %s", w)

        logger.info("%s", sep)


def _cli_debug(payload_source=None):
    """Pretty-print a JSON payload from stdin or file for debugging."""
    logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    if payload_source:
        with open(payload_source, "r") as f:
            body = json.load(f)
    else:
        body = json.load(sys.stdin)

    PayloadDebugMiddleware._log_payload(body)


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    _cli_debug(arg)
