"""Track C: Epigenomics — DNA Methylation (Bisulfite-seq) pipeline.

FastQC → Trimmomatic → Bismark alignment → Methylation extraction → MultiQC
"""

import glob
import logging
import os

from pipeline.tasks._constants import _TOOL_THREADS
from pipeline.tasks._genome import _resolve_bismark_genome, _resolve_genome
from pipeline.tasks._helpers import (
    _q,
    _run,
    _run_fastqc_step,
    _run_multiqc_step,
    _run_trim_step,
    _update_step,
)

logger = logging.getLogger(__name__)


def _run_bismark_align(trimmed_files, genome_dir, aligned_dir, library_type):
    """Track C (Methylation): Align bisulfite-converted reads with Bismark.

    Bismark handles the C→T and G→A base conversions inherent in bisulfite
    sequencing.  Uses Bowtie2 as the underlying aligner.

    Returns list of Bismark BAM output paths.
    """
    bam_files = []

    def _align_single(fq, stem):
        _run(
            f"bismark --bowtie2 --parallel {_TOOL_THREADS} "
            f"--genome {_q(genome_dir)} "
            f"-o {_q(aligned_dir)} "
            f"{_q(fq)}"
        )
        bismark_bam = os.path.join(
            aligned_dir,
            f"{os.path.basename(fq)}_bismark_bt2.bam"
        )
        final_bam = os.path.join(aligned_dir, f"{stem}.bam")
        if os.path.isfile(bismark_bam) and bismark_bam != final_bam:
            os.rename(bismark_bam, final_bam)
        _run(f"samtools index {_q(final_bam)}")
        return final_bam

    def _align_paired(r1, r2, prefix):
        _run(
            f"bismark --bowtie2 --parallel {_TOOL_THREADS} "
            f"--genome {_q(genome_dir)} "
            f"-o {_q(aligned_dir)} "
            f"-1 {_q(r1)} -2 {_q(r2)}"
        )
        bismark_bam = os.path.join(
            aligned_dir,
            f"{os.path.basename(r1)}_bismark_bt2_pe.bam"
        )
        final_bam = os.path.join(aligned_dir, f"{prefix}.bam")
        if os.path.isfile(bismark_bam) and bismark_bam != final_bam:
            os.rename(bismark_bam, final_bam)
        _run(f"samtools index {_q(final_bam)}")
        return final_bam

    if library_type == "paired":
        # Bismark paired-end runs sequentially (high memory per sample)
        for r1, r2, prefix in trimmed_files:
            bam_files.append(_align_paired(r1, r2, prefix))
    else:
        for fq, stem in trimmed_files:
            bam_files.append(_align_single(fq, stem))

    return bam_files


def _run_bismark_extract(bam_files, genome_dir, methyl_dir):
    """Track C (Methylation): Extract methylation calls from Bismark BAMs.

    Decodes C→T base pair mutations into methylation beta-values using
    bismark_methylation_extractor.
    """
    os.makedirs(methyl_dir, exist_ok=True)

    for bam in bam_files:
        _run(
            f"bismark_methylation_extractor "
            f"--comprehensive --merge_non_CpG "
            f"--cytosine_report --genome_folder {_q(genome_dir)} "
            f"--output {_q(methyl_dir)} "
            f"--parallel {max(1, _TOOL_THREADS // 2)} "
            f"{_q(bam)}"
        )

    report_files = (
        glob.glob(os.path.join(methyl_dir, "*.cov.gz"))
        + glob.glob(os.path.join(methyl_dir, "*.CX_report.txt"))
        + glob.glob(os.path.join(methyl_dir, "*.bedGraph.gz"))
    )
    return report_files


def _route_methylation(submission, job):
    """Track C: Epigenomics — DNA Methylation (Bisulfite-seq) pipeline.

    Bismark aligns bisulfite-converted DNA and mathematically decodes C→T
    base pair mutations into methylation beta-values. After extraction,
    runs methylKit for differential methylation analysis.
    """
    from pipeline.models import FileAsset
    from pipeline.stats._methylkit import run_differential_methylation
    from pipeline.tasks._routes import _register_stage2_assets

    work_dir = submission.upload_dir
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    methyl_dir = os.path.join(work_dir, "methylation")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (trimmed_dir, aligned_dir, methyl_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    fastq_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        ).values_list("local_path", flat=True)
    )
    library_type = submission.library_type
    genome_key = submission.reference_genome

    # Resolve genome FASTA directory + Bismark preparation
    is_custom = genome_key == "custom"
    _, genome_fasta, _ = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )

    # --- Step 0: Bismark genome preparation (if needed) ---
    _update_step(job, "bismark_prep")
    if is_custom:
        genome_dir = os.path.dirname(genome_fasta) if genome_fasta else None
        genome_dir = _resolve_bismark_genome(genome_dir=genome_dir, custom=True)
    else:
        genome_dir = _resolve_bismark_genome(genome_key=genome_key)
    _update_step(job, "bismark_prep", completed=True)

    # --- Step 1: FastQC ---
    _run_fastqc_step(job, fastq_assets, qc_dir)

    # --- Step 2: Trimmomatic ---
    trimmed_files = _run_trim_step(
        job, fastq_assets, trimmed_dir, library_type
    )

    # --- Step 3: Bismark alignment ---
    _update_step(job, "bismark_align")
    bam_files = _run_bismark_align(
        trimmed_files, genome_dir, aligned_dir, library_type
    )
    _update_step(job, "bismark_align", completed=True)

    # --- Step 4: Methylation extraction ---
    _update_step(job, "bismark_extract")
    report_files = _run_bismark_extract(bam_files, genome_dir, methyl_dir)
    for rf in report_files:
        FileAsset.objects.create(
            session_id=submission.session_id,
            submission=submission,
            file_role=FileAsset.FileRole.METHYLATION_REPORT,
            local_path=rf,
            is_user_uploaded=False,
        )
    _update_step(job, "bismark_extract", completed=True)

    # --- Step 5: MultiQC ---
    _run_multiqc_step(job, work_dir, qc_dir)

    # --- Stage 2: Differential methylation (methylKit) ---
    _update_step(job, "diff_methyl")
    stats_result = run_differential_methylation(submission)
    _update_step(job, "diff_methyl", completed=True)

    _register_stage2_assets(submission, stats_result, qc_dir=qc_dir)

    return {
        "methyl_dir": methyl_dir,
        "methylation_reports": report_files,
        "qc_dir": qc_dir,
        **stats_result,
    }
