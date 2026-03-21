"""Shared helpers used across all pipeline tracks.

Includes: shell execution, FASTQ pairing, progress tracking,
shared pipeline steps (FastQC, Trimmomatic, MultiQC), and BAM utilities.
"""

import logging
import os
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.tasks._constants import (
    _CPU_COUNT,
    _PARALLEL_SAMPLES,
    _TOOL_THREADS,
)

logger = logging.getLogger(__name__)


# ── Shell Execution ──────────────────────────────────────


def _run(cmd, cwd=None):
    """Execute a shell command, raising on failure.

    For piped commands, ``set -o pipefail`` is prepended so a failure
    at *any* stage of the pipeline is caught (e.g. HISAT2 failing before
    samtools sort receives data).
    """
    if "|" in cmd:
        cmd = "set -o pipefail; " + cmd
    logger.info("Running: %s", cmd)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True,
        executable="/bin/bash",  # pipefail requires bash, not sh
    )
    if result.returncode != 0:
        logger.error("STDERR: %s", result.stderr)
        raise RuntimeError(
            f"Command failed ({result.returncode}): {cmd}\n{result.stderr}"
        )
    return result


def _q(path):
    """Shell-quote a file path to prevent injection via special characters."""
    return shlex.quote(str(path))


# ── FASTQ Pairing ────────────────────────────────────────


def _pair_fastqs(fastq_paths):
    """Group paired-end FASTQs by prefix. Returns [(r1, r2), ...].

    Logs a warning for any unpaired files so the user knows samples
    were excluded. Raises if no valid pairs are found.
    """
    pairs = {}
    pattern = re.compile(
        r'^(.+?)(?:_R([12])|_([12]))\.(?:fq|fastq)\.gz$', re.IGNORECASE
    )
    for path in sorted(fastq_paths):
        name = os.path.basename(path)
        m = pattern.match(name)
        if m:
            prefix = m.group(1)
            read_num = m.group(2) or m.group(3)
            pairs.setdefault(prefix, {})[read_num] = path

    result = []
    for prefix in sorted(pairs):
        r1 = pairs[prefix].get("1")
        r2 = pairs[prefix].get("2")
        if r1 and r2:
            result.append((r1, r2))
        else:
            missing = "R2" if r1 else "R1"
            present = r1 or r2
            logger.warning(
                "Unpaired FASTQ dropped: %s is missing its %s mate. "
                "This sample will be excluded from the analysis.",
                os.path.basename(present), missing,
            )

    if not result:
        raise RuntimeError(
            "No valid paired-end FASTQ pairs found. Ensure files follow "
            "the naming convention: <sample>_R1.fq.gz / <sample>_R2.fq.gz"
        )
    return result


# ── Job Progress Tracking ─────────────────────────────────


def _update_step(job, step, completed=False):
    """Update step_progress on the job.

    Call with ``completed=False`` to mark a step as 'running', then
    ``completed=True`` when it finishes.  Also emits the update to the
    WebSocket group for real-time UI updates.
    """
    job.refresh_from_db(fields=["step_progress"])
    progress = job.step_progress or {}
    completed_steps = progress.get("completed_steps", [])

    if completed:
        if step not in completed_steps:
            completed_steps.append(step)
        progress["completed_steps"] = completed_steps
        progress["current_step"] = None
    else:
        progress["current_step"] = step
        progress["completed_steps"] = completed_steps

    job.step_progress = progress
    job.save(update_fields=["step_progress"])

    _emit_progress(job)


def _emit_progress(job):
    """Send the current job progress to the WebSocket group.

    Uses the synchronous channel layer API so it works inside Celery workers.
    Silently no-ops if the channel layer is unavailable.
    """
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer

        channel_layer = get_channel_layer()
        if channel_layer is None:
            return

        group_name = f"pipeline_{job.job_id}"
        payload = {
            "job_id": str(job.job_id),
            "status": job.status,
            "step_progress": job.step_progress,
        }
        # Include error detail so the frontend can show it immediately
        if job.status == "FAILED" and job.result_payload:
            payload["error"] = job.result_payload.get("error", "")

        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                "type": "pipeline.progress",
                "data": payload,
            },
        )
    except Exception:
        logger.debug(
            "Failed to emit WebSocket progress for job %s",
            job.job_id, exc_info=True,
        )


# ── Shared Pipeline Steps ────────────────────────────────


def _run_fastqc_step(job, fastq_paths, qc_dir):
    """Shared step: FastQC on raw reads."""
    _update_step(job, "fastqc")
    paths_str = " ".join(_q(p) for p in fastq_paths)
    _run(f"fastqc -o {_q(qc_dir)} -t {_CPU_COUNT} {paths_str}")
    _update_step(job, "fastqc", completed=True)


def _run_trim_step(job, fastq_assets, trimmed_dir, library_type, min_len=36):
    """Shared step: Trimmomatic adapter trimming.

    Args:
        min_len: Minimum read length after trimming. Use 18 for small RNA
                 (miRNA ~22 bp), 36 for standard RNA/ChIP/Methylation.

    Returns:
        For single-end: [(trimmed_path, stem), ...]
        For paired-end: [(r1_trimmed, r2_trimmed, prefix), ...]
    """
    _update_step(job, "trimmomatic")
    trimmed_files = []

    if library_type == "paired":
        pairs = _pair_fastqs(fastq_assets)

        def _trim_paired(r1, r2):
            prefix = os.path.basename(r1).split("_R1")[0].split("_1.")[0]
            out_r1 = os.path.join(trimmed_dir, f"{prefix}_R1_trimmed.fq.gz")
            out_r1_unpaired = os.path.join(trimmed_dir, f"{prefix}_R1_unpaired.fq.gz")
            out_r2 = os.path.join(trimmed_dir, f"{prefix}_R2_trimmed.fq.gz")
            out_r2_unpaired = os.path.join(trimmed_dir, f"{prefix}_R2_unpaired.fq.gz")
            _run(
                f"trimmomatic PE -threads {_TOOL_THREADS} "
                f"{_q(r1)} {_q(r2)} "
                f"{_q(out_r1)} {_q(out_r1_unpaired)} "
                f"{_q(out_r2)} {_q(out_r2_unpaired)} "
                f"ILLUMINACLIP:TruSeq3-PE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:{min_len}"
            )
            return (out_r1, out_r2, prefix)

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_trim_paired, r1, r2) for r1, r2 in pairs]
            for fut in as_completed(futures):
                trimmed_files.append(fut.result())
    else:
        def _trim_single(fq):
            name = os.path.basename(fq)
            stem = name.replace(".fq.gz", "").replace(".fastq.gz", "")
            out = os.path.join(trimmed_dir, f"{stem}_trimmed.fq.gz")
            _run(
                f"trimmomatic SE -threads {_TOOL_THREADS} "
                f"{_q(fq)} {_q(out)} "
                f"ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:{min_len}"
            )
            return (out, stem)

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [pool.submit(_trim_single, fq) for fq in fastq_assets]
            for fut in as_completed(futures):
                trimmed_files.append(fut.result())

    _update_step(job, "trimmomatic", completed=True)
    return trimmed_files


def _sort_and_index_bam(sam_or_bam, bam_out):
    """Shared helper: samtools sort + index a BAM/SAM file."""
    _sam_threads = max(2, _TOOL_THREADS // 2)
    _run(
        f"samtools sort -@ {_sam_threads} -o {_q(bam_out)} {_q(sam_or_bam)}"
    )
    _run(f"samtools index -@ {_sam_threads} {_q(bam_out)}")
    return bam_out


def _run_multiqc_step(job, work_dir, qc_dir):
    """Shared step: MultiQC report aggregation."""
    _update_step(job, "multiqc")
    _run(f"multiqc {_q(work_dir)} -o {_q(qc_dir)} --force --no-data-dir")
    _update_step(job, "multiqc", completed=True)


# ── Parameter Mapping Helpers ─────────────────────────────


def _strandedness_hisat2(strandedness, library_type):
    """Map strandedness + library type to HISAT2 --rna-strandness flag."""
    if strandedness == "unstranded":
        return ""
    if library_type == "paired":
        return (
            "--rna-strandness RF"
            if strandedness == "fr-firststrand"
            else "--rna-strandness FR"
        )
    return (
        "--rna-strandness R"
        if strandedness == "fr-firststrand"
        else "--rna-strandness F"
    )


def _strandedness_fc(strandedness):
    """Map strandedness to featureCounts -s flag (0/1/2)."""
    if strandedness == "fr-firststrand":
        return "2"
    if strandedness == "fr-secondstrand":
        return "1"
    return "0"


def _feature_type(quant_level):
    """Map quant_level to featureCounts -t flag."""
    return "transcript" if quant_level == "transcript" else "exon"


def _parse_metadata_csv(csv_path):
    """Parse the user-uploaded metadata CSV into a list of dicts."""
    import csv

    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows
