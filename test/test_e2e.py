"""Generate synthetic yeast RNA-Seq FASTQ data and run full E2E pipeline test.

Creates 6 single-end FASTQ samples (3 Control + 3 Treatment) from the yeast
genome, sets up a Django submission, and runs Stage 1 + Stage 2 directly.
"""

import gzip
import json
import os
import random
import subprocess
import sys
import uuid

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from pipeline.models import AnalysisSubmission, FileAsset, Session
from pipeline.tasks import _genome_paths

# Use the pre-built reference genome paths
_, GENOME_FA, GENOME_GTF = _genome_paths("r64")
# Session created later in main()
N_READS = 50000  # per sample — enough for alignment/counting
READ_LEN = 100


def parse_fasta(path):
    """Parse a FASTA file into {name: sequence} dict."""
    seqs = {}
    current = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith(">"):
                current = line.split()[0][1:]
                seqs[current] = []
            elif current:
                seqs[current].append(line)
    return {k: "".join(v) for k, v in seqs.items()}


def parse_gene_regions(gtf_path, max_genes=200):
    """Extract gene regions from GTF (chr, start, end, gene_id)."""
    genes = []
    seen = set()
    with open(gtf_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            fields = line.strip().split("\t")
            if len(fields) < 9 or fields[2] != "gene":
                continue
            chrom = fields[0]
            start = int(fields[3])
            end = int(fields[4])
            # Extract gene_id from attributes
            attrs = fields[8]
            gene_id = None
            for attr in attrs.split(";"):
                attr = attr.strip()
                if attr.startswith("gene_id"):
                    gene_id = attr.split('"')[1]
                    break
            if gene_id and gene_id not in seen:
                genes.append((chrom, start, end, gene_id))
                seen.add(gene_id)
                if len(genes) >= max_genes:
                    break
    return genes


def generate_reads_from_genes(genome, genes, n_reads, read_len, de_genes=None, fold_change=3.0):
    """Generate FASTQ reads from gene regions.

    de_genes: set of gene_ids that should get fold_change more reads.
    """
    rng = random.Random(42)
    reads = []

    # Weight genes: DE genes get more reads
    weights = []
    for _, start, end, gene_id in genes:
        w = fold_change if de_genes and gene_id in de_genes else 1.0
        w *= max(1, end - start)  # longer genes get more reads
        weights.append(w)

    total_w = sum(weights)
    gene_probs = [w / total_w for w in weights]

    for i in range(n_reads):
        # Pick a gene by weighted probability
        gene_idx = rng.choices(range(len(genes)), weights=gene_probs, k=1)[0]
        chrom, start, end, gene_id = genes[gene_idx]

        seq = genome.get(chrom, "")
        if not seq or end - start < read_len:
            continue

        # Pick a random position within the gene
        pos = rng.randint(start, end - read_len)
        read_seq = seq[pos:pos + read_len].upper()

        # Skip reads with N's
        if "N" in read_seq:
            continue

        # Generate realistic quality scores (Phred33, varying 20-40)
        qual_chars = "56789:;<=>?@ABCDEFGHI"  # Phred 20-40
        qual = "".join(rng.choices(qual_chars, k=len(read_seq)))

        reads.append((f"@read_{i}", read_seq, "+", qual))

    return reads


def write_fastq_gz(reads, path):
    """Write reads to a gzipped FASTQ file."""
    with gzip.open(path, "wt") as f:
        for name, seq, plus, qual in reads:
            f.write(f"{name}\n{seq}\n{plus}\n{qual}\n")


def main():
    print("=" * 60)
    print("RNAseek Full E2E Pipeline Test (Yeast)")
    print("=" * 60)

    # ── Parse genome and genes ──
    print("\n[1/6] Parsing yeast genome and gene annotations...")
    genome = parse_fasta(GENOME_FA)
    genes = parse_gene_regions(GENOME_GTF, max_genes=200)
    print(f"  Loaded {len(genome)} chromosomes, {len(genes)} gene regions")

    # Select 30 genes to be "differentially expressed" in Treatment
    de_genes = {g[3] for g in genes[:30]}
    print(f"  Selected {len(de_genes)} DE genes for Treatment condition")

    # ── Generate FASTQ files ──
    print("\n[2/6] Generating synthetic FASTQ files...")
    samples = {
        "Control_rep1": ("Control", False),
        "Control_rep2": ("Control", False),
        "Control_rep3": ("Control", False),
        "Treatment_rep1": ("Treatment", True),
        "Treatment_rep2": ("Treatment", True),
        "Treatment_rep3": ("Treatment", True),
    }

    # Create a Django submission
    # Create a Django session and submission
    session = Session.objects.create()
    SESSION_ID = str(session.session_id)

    sub = AnalysisSubmission.objects.create(
        session=session,
        library_type="single",
        strandedness="unstranded",
        reference_genome="r64",
        metadata_mode="manual",
        adjusted_pvalue=0.05,
        min_log2fc=-1.0,
        max_log2fc=1.0,
    )

    raw_dir = os.path.join(sub.upload_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    fastq_paths = []
    for sample_name, (condition, is_de) in samples.items():
        reads = generate_reads_from_genes(
            genome, genes, N_READS, READ_LEN,
            de_genes=de_genes if is_de else None,
            fold_change=4.0,
        )
        fq_path = os.path.join(raw_dir, f"{sample_name}.fq.gz")
        write_fastq_gz(reads, fq_path)
        fastq_paths.append(fq_path)
        print(f"  {sample_name}: {len(reads)} reads → {os.path.basename(fq_path)}")

        # Register as FileAsset
        FileAsset.objects.create(
            session=session,
            submission=sub,
            file_role=FileAsset.FileRole.RAW_FASTQ,
            local_path=fq_path,
            is_user_uploaded=True,
        )

    # ── Set up metadata payload ──
    print("\n[3/6] Configuring metadata and column mapping...")
    metadata_samples = []
    for sample_name, (condition, _) in samples.items():
        metadata_samples.append({
            "_sample_name": f"{sample_name}.fq.gz",
            "condition": condition,
        })

    sub.metadata_payload = {
        "samples": metadata_samples,
        "column_mapping": {
            "primary_group": "condition",
            "batch_effect": None,
            "additional_covariates": [],
        },
        "contrasts": [["Treatment", "Control"]],
    }
    sub.save()
    print(f"  Submission ID: {sub.submission_id}")
    print(f"  Work dir: {sub.upload_dir}")
    print(f"  Metadata: 6 samples, 2 conditions, 1 contrast")

    # ── Run Stage 1 directly ──
    print("\n[4/6] Running Stage 1: Alignment & Quantification...")
    print("  This may take a few minutes...")

    from pipeline.tasks import (
        _featurecounts_to_csv,
        _pair_fastqs,
        _run,
        _strandedness_fc,
        _strandedness_hisat2,
    )

    work_dir = sub.upload_dir
    trimmed_dir = os.path.join(work_dir, "trimmed")
    aligned_dir = os.path.join(work_dir, "aligned")
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")

    for d in (trimmed_dir, aligned_dir, counts_dir, qc_dir):
        os.makedirs(d, exist_ok=True)

    hisat2_idx = f"/home/littlekiddo/Desktop/RNA/genomes/r64/hisat2_index/r64"
    genome_fasta = GENOME_FA
    genome_gtf = GENOME_GTF

    # Step 1: FastQC
    print("  → FastQC...")
    _run(f"fastqc -o {qc_dir} -t 8 {' '.join(fastq_paths)}")

    # Step 2: Trimmomatic (single-end)
    print("  → Trimmomatic...")
    trimmed_files = []
    for fq in fastq_paths:
        name = os.path.basename(fq)
        stem = name.replace(".fq.gz", "").replace(".fastq.gz", "")
        out = os.path.join(trimmed_dir, f"{stem}_trimmed.fq.gz")
        _run(
            f"trimmomatic SE -threads 8 {fq} {out} "
            f"ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 "
            f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
        )
        trimmed_files.append((out, stem))

    # Step 3: HISAT2 → CRAM
    print("  → HISAT2 alignment...")
    cram_files = []
    for fq, stem in trimmed_files:
        cram_path = os.path.join(aligned_dir, f"{stem}.cram")
        _run(
            f"hisat2 -p 8 --dta "
            f"-x {hisat2_idx} -U {fq} "
            f"| samtools view -C -T {genome_fasta} -@ 4 "
            f"| samtools sort -@ 4 -o {cram_path}"
        )
        _run(f"samtools index {cram_path}")
        cram_files.append(cram_path)
    print(f"  → Aligned {len(cram_files)} samples")

    # Step 4: featureCounts (convert CRAM→BAM first as featureCounts 2.1.1 needs BAM)
    print("  → Converting CRAM → BAM for featureCounts...")
    bam_files = []
    for cram in cram_files:
        bam_path = cram.replace(".cram", ".bam")
        _run(f"samtools view -b -T {genome_fasta} -@ 4 -o {bam_path} {cram}")
        _run(f"samtools index {bam_path}")
        bam_files.append(bam_path)

    print("  → featureCounts...")
    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")
    fc_output = os.path.join(counts_dir, "featurecounts_output.txt")
    _run(
        f"featureCounts "
        f"-T 8 -s 0 -t exon -g gene_id "
        f"-a {genome_gtf} "
        f"-o {fc_output} "
        f"{' '.join(bam_files)}"
    )
    _featurecounts_to_csv(fc_output, count_matrix_path)

    FileAsset.objects.create(
        session=session,
        submission=sub,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=count_matrix_path,
        is_user_uploaded=False,
    )

    # Step 5: MultiQC
    print("  → MultiQC...")
    _run(f"multiqc {work_dir} -o {qc_dir} --force --no-data-dir")

    print("  ✓ Stage 1 complete!")

    # ── Run Stage 2 ──
    print("\n[5/6] Running Stage 2: DESeq2 Statistical Analysis...")
    from pipeline.stats import run_stage2_stats

    stats_result = run_stage2_stats(sub)
    print(f"  ✓ Stage 2 complete!")
    print(f"  Stats dir: {stats_result['stats_dir']}")
    print(f"  DEG results: {stats_result['deg_results']}")
    print(f"  Batch corrected: {stats_result['batch_corrected']}")

    # ── Validate results ──
    print("\n[6/6] Validating results...")
    import pandas as pd

    passed = 0
    failed = 0

    def check(name, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} — {detail}")

    # Count matrix
    counts = pd.read_csv(count_matrix_path)
    check("Count matrix exists", os.path.exists(count_matrix_path))
    check("Has gene_id column", "gene_id" in counts.columns)
    check("Has 6 sample columns", len(counts.columns) == 7)  # gene_id + 6 samples
    check("Has genes with counts", (counts.iloc[:, 1:].sum().sum() > 0))
    print(f"    → {len(counts)} genes, {counts.iloc[:, 1:].sum().sum():.0f} total counts")

    # Normalized counts
    check("Normalized counts exist", os.path.exists(stats_result["normalized_counts"]))

    # DEG results
    for deg_path in stats_result["deg_results"]:
        deg = pd.read_csv(deg_path)
        check(f"DEG file: {os.path.basename(deg_path)}", os.path.exists(deg_path))
        check("Has padj column", "padj" in deg.columns)
        check("Has log2FoldChange", "log2FoldChange" in deg.columns)
        check("Has significant column", "significant" in deg.columns)
        n_sig = deg["significant"].sum()
        print(f"    → {len(deg)} genes tested, {n_sig} significant (padj≤0.05, |log2FC|≥1)")

    # Outlier flags
    check("Outlier flags exist", "outlier_flags" in stats_result)

    # QC reports
    check("MultiQC report exists", os.path.exists(os.path.join(qc_dir, "multiqc_report.html")))

    # CRAM files
    for cram in cram_files:
        check(f"CRAM: {os.path.basename(cram)}", os.path.exists(cram))

    print(f"\n{'='*60}")
    print(f"E2E Results: {passed} passed, {failed} failed")
    print(f"{'='*60}")

    # Cleanup
    print(f"\nTest submission ID: {sub.submission_id}")
    print(f"Session ID: {SESSION_ID}")
    print(f"Work directory: {work_dir}")

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
