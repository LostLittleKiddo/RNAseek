"""Track B: Regulatory Transcriptomics (Small RNA / miRNA).

FastQC → Trimmomatic (MINLEN:18) → Bowtie (miRBase) → miRNA quantification → MultiQC → Stage 2
"""

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline.tasks._constants import _PARALLEL_SAMPLES, _TOOL_THREADS
from pipeline.tasks._genome import _resolve_mirbase
from pipeline.tasks._helpers import (
    _q,
    _run,
    _run_fastqc_step,
    _run_multiqc_step,
    _run_trim_step,
    _sort_and_index_bam,
    _update_step,
)
from pipeline.tasks._routes import _register_stage2_assets

logger = logging.getLogger(__name__)


def _run_bowtie_mirna(trimmed_files, mirbase_idx, aligned_dir, library_type):
    """Track B: Align small RNA reads to miRBase using Bowtie.

    Bowtie (v1) is optimized for ultra-short reads (~22 bp miRNA).
    Flags:
      -v 1        — Allow 1 mismatch across the entire read
      --best      — Report alignments in best-first order
      --strata    — Only report alignments in the best stratum
      -m 5        — Suppress reads mapping to >5 locations
      --norc      — Do not align to reverse complement (miRNAs are strand-specific)
      -S          — Output SAM format
      -p threads  — Multi-threaded

    Returns list of sorted, indexed BAM paths.
    """
    bam_files = []

    def _align_single(fq, stem):
        sam_path = os.path.join(aligned_dir, f"{stem}.sam")
        bam_path = os.path.join(aligned_dir, f"{stem}.bam")
        _run(
            f"bowtie -v 1 --best --strata -m 5 --norc -S "
            f"-p {_TOOL_THREADS} "
            f"{_q(mirbase_idx)} {_q(fq)} {_q(sam_path)}"
        )
        _sort_and_index_bam(sam_path, bam_path)
        os.remove(sam_path)
        return bam_path

    def _align_paired(r1, r2, prefix):
        sam_path = os.path.join(aligned_dir, f"{prefix}.sam")
        bam_path = os.path.join(aligned_dir, f"{prefix}.bam")
        _run(
            f"bowtie -v 1 --best --strata -m 5 --norc -S "
            f"-p {_TOOL_THREADS} "
            f"{_q(mirbase_idx)} -1 {_q(r1)} -2 {_q(r2)} {_q(sam_path)}"
        )
        _sort_and_index_bam(sam_path, bam_path)
        os.remove(sam_path)
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


def _mirna_counts_from_bams(bam_files, counts_dir):
    """Generate a miRNA count matrix from BAMs aligned to miRBase.

    Each reference sequence in the miRBase index is a single miRNA.
    ``samtools idxstats`` yields per-reference mapped read counts, which
    directly gives us miRNA-level expression values.

    Returns the path to the CSV count matrix.
    """
    import pandas as pd

    all_counts = {}
    for bam in bam_files:
        sample_name = os.path.basename(bam).replace(".bam", "")
        result = _run(f"samtools idxstats {_q(bam)}")
        counts = {}
        for line in result.stdout.strip().split("\n"):
            parts = line.split("\t")
            if len(parts) >= 3 and parts[0] != "*":
                mirna_id = parts[0]
                mapped = int(parts[2])
                if mapped > 0:
                    counts[mirna_id] = mapped
        all_counts[sample_name] = counts

    df = pd.DataFrame(all_counts).fillna(0).astype(int)
    df.index.name = "gene_id"

    os.makedirs(counts_dir, exist_ok=True)
    path = os.path.join(counts_dir, "raw_counts.csv")
    df.to_csv(path)
    return path


def _route_small_rna(submission, job):
    """Track B: Regulatory Transcriptomics — Small RNA / miRNA pipeline.

    Maps against the specialized miRBase database instead of the whole genome.
    Bowtie v1 is used because it efficiently handles ultra-short reads.
    """
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
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
    genome_key = submission.reference_genome

    # Resolve miRBase Bowtie index
    mirbase_idx = _resolve_mirbase(genome_key)

    # --- Step 1: FastQC ---
    _run_fastqc_step(job, fastq_assets, qc_dir)

    # --- Step 2: Trimmomatic (MINLEN:18 for miRNA ~22 bp reads) ---
    trimmed_files = _run_trim_step(
        job, fastq_assets, trimmed_dir, library_type, min_len=18
    )

    # --- Step 3: Bowtie alignment against miRBase ---
    _update_step(job, "bowtie_mirna")
    bam_files = _run_bowtie_mirna(
        trimmed_files, mirbase_idx, aligned_dir, library_type
    )
    _update_step(job, "bowtie_mirna", completed=True)

    # --- Step 4: miRNA quantification ---
    _update_step(job, "mirna_quantify")
    count_matrix_path = _mirna_counts_from_bams(bam_files, counts_dir)
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )
    _update_step(job, "mirna_quantify", completed=True)

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
