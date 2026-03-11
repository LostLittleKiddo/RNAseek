"""Track C: Epigenomics — ChIP-seq peak calling pipeline.

FastQC → Trimmomatic → BWA MEM → MACS2 peak calling → MultiQC
"""

import glob
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.tasks._constants import _MACS2_GENOME_SIZE, _PARALLEL_SAMPLES, _TOOL_THREADS
from pipeline.tasks._genome import _resolve_bwa_index, _resolve_genome
from pipeline.tasks._helpers import (
    _q,
    _run,
    _run_fastqc_step,
    _run_multiqc_step,
    _run_trim_step,
    _update_step,
)

logger = logging.getLogger(__name__)


def _run_bwa_align(trimmed_files, genome_fasta, aligned_dir, library_type):
    """Track C (ChIP-seq): Align reads using BWA MEM.

    BWA MEM is the standard aligner for ChIP-seq reads (50–150 bp).
    Outputs are sorted, indexed BAM files.

    Returns list of BAM paths.
    """
    bam_files = []
    _sam_threads = max(2, _TOOL_THREADS // 2)

    def _align_single(fq, stem):
        bam_path = os.path.join(aligned_dir, f"{stem}.bam")
        _run(
            f"bwa mem -t {_TOOL_THREADS} {_q(genome_fasta)} {_q(fq)} "
            f"| samtools view -b -@ {_sam_threads} "
            f"| samtools sort -@ {_sam_threads} -o {_q(bam_path)}"
        )
        _run(f"samtools index -@ {_sam_threads} {_q(bam_path)}")
        return bam_path

    def _align_paired(r1, r2, prefix):
        bam_path = os.path.join(aligned_dir, f"{prefix}.bam")
        _run(
            f"bwa mem -t {_TOOL_THREADS} {_q(genome_fasta)} {_q(r1)} {_q(r2)} "
            f"| samtools view -b -@ {_sam_threads} "
            f"| samtools sort -@ {_sam_threads} -o {_q(bam_path)}"
        )
        _run(f"samtools index -@ {_sam_threads} {_q(bam_path)}")
        return bam_path

    if library_type == "paired":
        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [
                pool.submit(_align_paired, r1, r2, pf)
                for r1, r2, pf in trimmed_files
            ]
            for fut in as_completed(futures):
                bam_files.append(fut.result())
    else:
        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [
                pool.submit(_align_single, fq, stem)
                for fq, stem in trimmed_files
            ]
            for fut in as_completed(futures):
                bam_files.append(fut.result())

    return bam_files


def _run_macs2_callpeak(treatment_bams, control_bams, peaks_dir, genome_key,
                        library_type):
    """Track C (ChIP-seq): Call peaks using MACS2.

    Identifies genomic regions where transcription factors are bound to DNA.
    Control (input) BAMs are optional but recommended for background correction.

    Returns list of peak file paths (narrowPeak format).
    """
    os.makedirs(peaks_dir, exist_ok=True)
    gsize = _MACS2_GENOME_SIZE.get(genome_key, "hs")
    fmt = "BAMPE" if library_type == "paired" else "BAM"

    treatment_str = " ".join(_q(b) for b in treatment_bams)
    control_flag = ""
    if control_bams:
        control_str = " ".join(_q(b) for b in control_bams)
        control_flag = f"-c {control_str}"

    _run(
        f"macs2 callpeak "
        f"-t {treatment_str} {control_flag} "
        f"-f {fmt} -g {gsize} "
        f"--outdir {_q(peaks_dir)} -n chipseq "
        f"--keep-dup auto -q 0.05"
    )

    peak_files = (
        glob.glob(os.path.join(peaks_dir, "*.narrowPeak"))
        + glob.glob(os.path.join(peaks_dir, "*.broadPeak"))
    )
    return peak_files


def _split_chip_samples(bam_files, trimmed_files, submission, library_type):
    """Separate ChIP-seq BAMs into treatment (IP) and control (Input).

    Uses the metadata condition column: samples whose condition matches
    'input' (case-insensitive) are control; everything else is treatment.
    """
    payload = submission.metadata_payload or {}
    samples = payload.get("samples", [])
    column_mapping = payload.get("column_mapping", {})
    primary_group = column_mapping.get("primary_group", "condition")

    # Build a set of control sample stems (use regex for robust stripping)
    control_stems = set()
    sample_col = "_sample_name" if any("_sample_name" in s for s in samples) else (
        list(samples[0].keys())[0] if samples else "sample"
    )
    for row in samples:
        condition = str(row.get(primary_group, "")).strip().lower()
        if condition == "input":
            stem = row.get(sample_col, "")
            # Strip file extensions and read-pair suffixes with regex
            stem = re.sub(r'(?:\.fq|\.fastq)(?:\.gz)?$', '', stem, flags=re.IGNORECASE)
            stem = re.sub(r'_R[12]$', '', stem)
            control_stems.add(stem)

    # Partition BAM files based on stem matching
    treatment_bams = []
    control_bams = []
    for bam in bam_files:
        bam_stem = os.path.basename(bam).replace(".bam", "")
        bam_stem_clean = bam_stem.replace("_trimmed", "")
        if bam_stem_clean in control_stems:
            control_bams.append(bam)
        else:
            treatment_bams.append(bam)

    if not treatment_bams:
        raise RuntimeError(
            "No treatment (IP) samples found. All samples appear to be "
            "labeled as 'input'. At least one non-input sample is required."
        )

    return treatment_bams, control_bams


def _route_chip_seq(submission, job):
    """Track C: Epigenomics — ChIP-seq peak calling pipeline.

    Samples labeled as 'input' (case-insensitive) in the metadata condition
    column are used as MACS2 control.  All other samples are treatment (IP).
    """
    from pipeline.models import FileAsset

    work_dir = submission.upload_dir
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    peaks_dir = os.path.join(work_dir, "peaks")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (trimmed_dir, aligned_dir, peaks_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    fastq_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        ).values_list("local_path", flat=True)
    )
    library_type = submission.library_type
    genome_key = submission.reference_genome

    # Resolve genome FASTA + BWA index
    _, genome_fasta, _ = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )
    genome_fasta = _resolve_bwa_index(genome_fasta)

    # --- Step 1: FastQC ---
    _run_fastqc_step(job, fastq_assets, qc_dir)

    # --- Step 2: Trimmomatic ---
    trimmed_files = _run_trim_step(
        job, fastq_assets, trimmed_dir, library_type
    )

    # --- Step 3: BWA MEM alignment ---
    _update_step(job, "bwa_align")
    bam_files = _run_bwa_align(
        trimmed_files, genome_fasta, aligned_dir, library_type
    )
    _update_step(job, "bwa_align", completed=True)

    # --- Step 4: MACS2 peak calling ---
    _update_step(job, "macs2_peaks")
    treatment_bams, control_bams = _split_chip_samples(
        bam_files, trimmed_files, submission, library_type
    )
    peak_files = _run_macs2_callpeak(
        treatment_bams, control_bams, peaks_dir, genome_key, library_type
    )
    for pf in peak_files:
        FileAsset.objects.create(
            session_id=submission.session_id,
            submission=submission,
            file_role=FileAsset.FileRole.PEAK_FILE,
            local_path=pf,
            is_user_uploaded=False,
        )
    _update_step(job, "macs2_peaks", completed=True)

    # --- Step 5: MultiQC ---
    _run_multiqc_step(job, work_dir, qc_dir)

    return {
        "peaks_dir": peaks_dir,
        "peak_files": peak_files,
        "qc_dir": qc_dir,
    }
