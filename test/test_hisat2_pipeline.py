"""Pipeline test: run real HISAT2 alignment with the rnaseek_dev_dataset FASTQs.

Uses the 2 FASTQ files in rnaseek_dev_dataset/ to run the full Stage 1 pipeline
(FastQC -> Trimmomatic -> HISAT2 -> featureCounts -> MultiQC) and then Stage 2
(ComBat_seq + DESeq2).

This tests that each bioinformatics tool works correctly with real yeast reads.

Samples:
  - GSM9346166_Unstressed_Rep1_dev.fastq.gz → unstressed control (0 min)
  - GSM9346170_NaCl_Rep1_dev.fastq.gz       → 0.4 M NaCl (45 min)
"""

import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from pipeline.models import AnalysisSubmission, FileAsset, Session
from pipeline.tasks import _genome_paths

DEV_DIR = os.path.join(os.path.dirname(__file__), "..", "rnaseek_dev_dataset")
FASTQ_FILES = [
    os.path.join(DEV_DIR, "GSM9346166_Unstressed_Rep1_dev.fastq.gz"),
    os.path.join(DEV_DIR, "GSM9346170_NaCl_Rep1_dev.fastq.gz"),
]
# Use the pre-built reference genome paths
HISAT2_IDX, _, GENOME_GTF = _genome_paths("r64")

PASS = 0
FAIL = 0


def report(name, passed, detail=""):
    global PASS, FAIL
    if passed:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} — {detail}")


def main():
    print("=" * 60)
    print("RNAseek Dev Dataset HISAT2 Pipeline Test")
    print("  2-sample yeast (real FASTQ → full Stage 1 + Stage 2)")
    print("=" * 60)

    # ── Preflight checks ──
    print("\n[0/7] Preflight checks...")
    for fq in FASTQ_FILES:
        report(f"FASTQ exists: {os.path.basename(fq)}", os.path.exists(fq))
    report("HISAT2 index exists", os.path.exists(HISAT2_IDX + ".1.ht2"))
    report("GTF exists", os.path.exists(GENOME_GTF))

    # Check tools are available
    from pipeline.tasks import _run
    for tool in ["fastqc", "trimmomatic", "hisat2", "samtools", "featureCounts", "multiqc"]:
        try:
            _run(f"which {tool}")
            report(f"{tool} on PATH", True)
        except RuntimeError:
            report(f"{tool} on PATH", False, "not found")

    # ── Step 1: Set up Django submission ──
    print("\n[1/7] Setting up Django submission (FASTQ entry)...")
    session = Session.objects.create()
    sub = AnalysisSubmission.objects.create(
        session=session,
        input_data_type="fastq",
        library_type="single",
        strandedness="unstranded",
        reference_genome="r64",
        metadata_mode="upload",
        adjusted_pvalue=0.05,
        min_log2fc=-1.0,
        max_log2fc=1.0,
    )

    work_dir = sub.upload_dir
    raw_dir = os.path.join(work_dir, "raw")
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (raw_dir, trimmed_dir, aligned_dir, counts_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    # Copy FASTQs into submission raw dir and register as FileAssets
    fastq_paths = []
    for fq in FASTQ_FILES:
        dest = os.path.join(raw_dir, os.path.basename(fq))
        shutil.copy2(fq, dest)
        fastq_paths.append(dest)
        FileAsset.objects.create(
            session=session,
            submission=sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=dest,
            is_user_uploaded=True,
        )

    # Metadata: use FASTQ filenames as _sample_name (matches featureCounts output)
    sub.metadata_payload = {
        "samples": [
            {
                "_sample_name": "GSM9346166_Unstressed_Rep1_dev.fastq.gz",
                "condition": "unstressed control (0 min)",
                "batch": "UC_HiSeq4000",
            },
            {
                "_sample_name": "GSM9346170_NaCl_Rep1_dev.fastq.gz",
                "condition": "0.4 M NaCl (45 min)",
                "batch": "UC_HiSeq4000",
            },
        ],
        "column_mapping": {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": [],
        },
        "contrasts": [
            ["0.4 M NaCl (45 min)", "unstressed control (0 min)"],
        ],
    }
    sub.save()

    print(f"    Submission ID: {sub.submission_id}")
    print(f"    Work dir: {work_dir}")

    # ── Step 2: FastQC ──
    print("\n[2/7] Running FastQC on raw FASTQs...")
    from pipeline.tasks import _CPU_COUNT
    t0 = time.time()
    _run(f"fastqc -o {qc_dir} -t {_CPU_COUNT} {' '.join(fastq_paths)}")
    elapsed = time.time() - t0
    fastqc_zips = [f for f in os.listdir(qc_dir) if f.endswith("_fastqc.zip")]
    report(f"FastQC produced {len(fastqc_zips)} reports", len(fastqc_zips) == 2)
    print(f"    Elapsed: {elapsed:.1f}s")

    # ── Step 3: Trimmomatic ──
    print("\n[3/7] Running Trimmomatic (single-end)...")
    from pipeline.tasks import _TOOL_THREADS
    t0 = time.time()
    trimmed_files = []
    for fq in fastq_paths:
        name = os.path.basename(fq)
        stem = name.replace(".fq.gz", "").replace(".fastq.gz", "")
        out = os.path.join(trimmed_dir, f"{stem}_trimmed.fq.gz")
        _run(
            f"trimmomatic SE -threads {_TOOL_THREADS} {fq} {out} "
            f"ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 "
            f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
        )
        trimmed_files.append((out, stem))
    elapsed = time.time() - t0
    for out, stem in trimmed_files:
        report(f"Trimmed: {stem}", os.path.exists(out))
    print(f"    Elapsed: {elapsed:.1f}s")

    # ── Step 4: HISAT2 → BAM ──
    print("\n[4/7] Running HISAT2 alignment...")
    t0 = time.time()
    _sam_threads = max(2, _TOOL_THREADS // 2)
    bam_files = []
    for fq, stem in trimmed_files:
        bam_path = os.path.join(aligned_dir, f"{stem}.bam")
        _run(
            f"hisat2 -p {_TOOL_THREADS} --dta "
            f"-x {HISAT2_IDX} -U {fq} "
            f"| samtools view -b -@ {_sam_threads} "
            f"| samtools sort -@ {_sam_threads} -o {bam_path}"
        )
        _run(f"samtools index -@ {_sam_threads} {bam_path}")
        bam_files.append(bam_path)
    elapsed = time.time() - t0
    for bam in bam_files:
        report(f"BAM: {os.path.basename(bam)}", os.path.exists(bam))
        # Verify BAM is non-empty
        result = _run(f"samtools view -c {bam}")
        n_reads = int(result.stdout.strip())
        report(f"  Aligned reads > 0", n_reads > 0, f"got {n_reads}")
        print(f"    {os.path.basename(bam)}: {n_reads:,} aligned reads")
    print(f"    Elapsed: {elapsed:.1f}s")

    # ── Step 5: featureCounts ──
    print("\n[5/7] Running featureCounts...")
    from pipeline.tasks import _featurecounts_to_csv
    t0 = time.time()
    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")
    fc_output = os.path.join(counts_dir, "featurecounts_output.txt")
    _run(
        f"featureCounts "
        f"-T {_CPU_COUNT} -s 0 -t exon -g gene_id "
        f"-a {GENOME_GTF} "
        f"-o {fc_output} "
        f"{' '.join(bam_files)}"
    )
    _featurecounts_to_csv(fc_output, count_matrix_path)
    elapsed = time.time() - t0

    import pandas as pd
    counts_df = pd.read_csv(count_matrix_path)
    report("Count matrix exists", os.path.exists(count_matrix_path))
    report("Has gene_id column", "gene_id" in counts_df.columns)
    report("Has 2 sample columns", len(counts_df.columns) == 3,
           f"got {len(counts_df.columns)} columns: {list(counts_df.columns)}")
    total_counts = counts_df.iloc[:, 1:].sum().sum()
    report("Has counts > 0", total_counts > 0, f"total = {total_counts}")
    n_genes_with_counts = (counts_df.iloc[:, 1:].sum(axis=1) > 0).sum()
    print(f"    {len(counts_df)} genes, {n_genes_with_counts} with counts > 0")
    print(f"    Total counts: {total_counts:,.0f}")
    print(f"    Elapsed: {elapsed:.1f}s")

    # Register count matrix
    FileAsset.objects.create(
        session=session,
        submission=sub,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )

    # ── Step 6: MultiQC ──
    print("\n[6/7] Running MultiQC...")
    t0 = time.time()
    _run(f"multiqc {work_dir} -o {qc_dir} --force --no-data-dir")
    elapsed = time.time() - t0
    report("MultiQC report exists",
           os.path.exists(os.path.join(qc_dir, "multiqc_report.html")))
    print(f"    Elapsed: {elapsed:.1f}s")

    # ── Step 7: Stage 2 (DESeq2) ──
    # DESeq2 requires biological replicates (>=2 samples per condition).
    # With only 2 total samples (1 per group), dispersion estimation is
    # impossible — DESeq2 v1.22+ raises an error.  This is expected; the
    # test validates that the pipeline detects the situation cleanly.
    print("\n[7/7] Running Stage 2: DESeq2 (expect replicate warning)...")
    t0 = time.time()
    from pipeline.stats import run_stage2_stats
    try:
        stats_result = run_stage2_stats(sub)
        # If it somehow succeeds, validate outputs
        report("Normalized counts exist", os.path.exists(stats_result["normalized_counts"]))
        report("DEG results produced", len(stats_result["deg_results"]) >= 1)
        for deg_path in stats_result["deg_results"]:
            fname = os.path.basename(deg_path)
            deg_df = pd.read_csv(deg_path)
            report(f"DEG file: {fname}", os.path.exists(deg_path))
            n_sig = deg_df["significant"].sum()
            print(f"    {fname}: {len(deg_df)} genes, {n_sig} significant")
    except RuntimeError as exc:
        if "replicates" in str(exc).lower() or "coefficients to fit" in str(exc).lower():
            report("DESeq2 correctly requires replicates (2 samples insufficient)", True)
            print(f"    (Expected) {exc}")
        else:
            report("DESeq2 failed unexpectedly", False, str(exc))
    elapsed = time.time() - t0
    print(f"    Elapsed: {elapsed:.1f}s")

    # ── Summary ──
    print(f"\n{'=' * 60}")
    print(f"HISAT2 Pipeline Test Results: {PASS} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    print(f"\nSubmission: {sub.submission_id}")
    print(f"Work dir: {work_dir}")

    return FAIL == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
