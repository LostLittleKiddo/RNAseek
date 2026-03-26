"""Track A: Standard Transcriptomics (Poly-A RNA-Seq).

FastQC → Trimmomatic → HISAT2 → featureCounts → MultiQC → Stage 2 (DESeq2)
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.tasks._constants import _CPU_COUNT, _PARALLEL_SAMPLES, _TOOL_THREADS
from pipeline.tasks._featurecounts import _run_featurecounts
from pipeline.tasks._genome import _resolve_genome
from pipeline.tasks._helpers import (
    _q,
    _run,
    _run_fastqc_step,
    _run_multiqc_step,
    _run_trim_step,
    _strandedness_hisat2,
    _update_step,
)
from pipeline.tasks._routes import _register_stage2_assets

logger = logging.getLogger(__name__)


def _route_fastq(submission, job):
    """Route A: Full pipeline from FASTQ files."""
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    raw_dir = os.path.join(work_dir, "raw")
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (trimmed_dir, aligned_dir, counts_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    fastq_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.RAW_FASTQ
        ).values_list("local_path", flat=True)
    )

    library_type = submission.library_type
    strandedness = submission.strandedness
    quant_level = submission.metadata_payload.get("quant_level", "gene")

    genome_key = submission.reference_genome
    hisat2_idx, genome_fasta, genome_gtf = _resolve_genome(
        genome_key, work_dir, submission=submission,
    )

    # --- Step 0 (custom only): Build HISAT2 index from user FASTA ---
    if genome_key == "custom":
        _update_step(job, "hisat2_build")
        if not genome_fasta or not os.path.isfile(genome_fasta):
            raise RuntimeError(
                f"Custom genome FASTA file not found. "
                f"Expected path: {genome_fasta or 'N/A'}. "
                f"If you uploaded a .fa.zip file, it may be corrupt or empty."
            )
        idx_dir = os.path.join(work_dir, "custom_genome", "hisat2_index")
        os.makedirs(idx_dir, exist_ok=True)
        idx_prefix = os.path.join(idx_dir, "genome")
        try:
            _run(f"hisat2-build -p {_CPU_COUNT} {_q(genome_fasta)} {_q(idx_prefix)}")
        except RuntimeError as exc:
            raise RuntimeError(
                f"HISAT2 index build failed for '{os.path.basename(genome_fasta)}'. "
                f"Ensure the FASTA file is valid, uncompressed, and not truncated. "
                f"Detail: {exc}"
            ) from exc
        hisat2_idx = idx_prefix
        _update_step(job, "hisat2_build", completed=True)

    # --- Step 1: FastQC on raw FASTQs ---
    _run_fastqc_step(job, fastq_assets, qc_dir)

    # --- Step 2: Trimmomatic ---
    trimmed_files = _run_trim_step(job, fastq_assets, trimmed_dir, library_type)

    # --- Step 3: HISAT2 → samtools BAM (parallel per sample) ---
    _update_step(job, "hisat2")
    strand_flag = _strandedness_hisat2(strandedness, library_type)
    bam_files = []
    _sam_threads = max(2, _TOOL_THREADS // 2)

    if library_type == "paired":
        def _align_paired(r1, r2, prefix):
            bam_path = os.path.join(aligned_dir, f"{prefix}.bam")
            _run(
                f"hisat2 -p {_TOOL_THREADS} --dta {strand_flag} "
                f"-x {_q(hisat2_idx)} -1 {_q(r1)} -2 {_q(r2)} "
                f"| samtools view -b -@ {_sam_threads} "
                f"| samtools sort -@ {_sam_threads} -o {_q(bam_path)}"
            )
            _run(f"samtools index -@ {_sam_threads} {_q(bam_path)}")
            return bam_path

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [
                pool.submit(_align_paired, r1, r2, pf)
                for r1, r2, pf in trimmed_files
            ]
            for fut in as_completed(futures):
                bam_files.append(fut.result())
    else:
        def _align_single(fq, stem):
            bam_path = os.path.join(aligned_dir, f"{stem}.bam")
            _run(
                f"hisat2 -p {_TOOL_THREADS} --dta {strand_flag} "
                f"-x {_q(hisat2_idx)} -U {_q(fq)} "
                f"| samtools view -b -@ {_sam_threads} "
                f"| samtools sort -@ {_sam_threads} -o {_q(bam_path)}"
            )
            _run(f"samtools index -@ {_sam_threads} {_q(bam_path)}")
            return bam_path

        with ThreadPoolExecutor(max_workers=_PARALLEL_SAMPLES) as pool:
            futures = [
                pool.submit(_align_single, fq, stem)
                for fq, stem in trimmed_files
            ]
            for fut in as_completed(futures):
                bam_files.append(fut.result())
    _update_step(job, "hisat2", completed=True)

    # Register aligned BAM files as downloadable assets
    for bam_path in bam_files:
        FileAsset.objects.create(
            session_id=submission.session_id,
            submission=submission,
            file_role=FileAsset.FileRole.ALIGNMENT_BAM,
            local_path=bam_path,
            is_user_uploaded=False,
        )

    # --- Step 4: featureCounts ---
    _update_step(job, "featurecounts")
    count_matrix_path = _run_featurecounts(
        bam_files, genome_gtf, strandedness, quant_level, library_type, work_dir
    )
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )
    _update_step(job, "featurecounts", completed=True)

    # --- Step 5: MultiQC ---
    _run_multiqc_step(job, work_dir, qc_dir)

    # --- Stage 2: Statistical analysis ---
    _update_step(job, "deseq2")
    stats_result = run_stage2_stats(submission)
    _update_step(job, "deseq2", completed=True)

    _register_stage2_assets(submission, stats_result, qc_dir=qc_dir)

    return {
        "count_matrix": count_matrix_path,
        "qc_dir": qc_dir,
        **stats_result,
    }
