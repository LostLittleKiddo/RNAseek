import csv
import glob
import json
import logging
import os
import subprocess

from celery import shared_task

logger = logging.getLogger(__name__)

# ── Paths to pre-indexed reference genomes (configurable via settings) ──
_GENOME_BASE = os.environ.get("RNASEEK_GENOME_DIR", "/app/genomes")


def _genome_paths(genome_key):
    """Return (hisat2_index_prefix, fasta_path, gtf_path) for a genome key."""
    base = os.path.join(_GENOME_BASE, genome_key)
    return (
        os.path.join(base, "hisat2_index", genome_key),  # HISAT2 index prefix
        os.path.join(base, f"{genome_key}.fa"),
        os.path.join(base, f"{genome_key}.gtf"),
    )


def _run(cmd, cwd=None):
    """Execute a shell command, raising on failure."""
    logger.info("Running: %s", cmd)
    result = subprocess.run(
        cmd, shell=True, cwd=cwd,
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("STDERR: %s", result.stderr)
        raise RuntimeError(f"Command failed ({result.returncode}): {cmd}\n{result.stderr}")
    return result


def _strandedness_hisat2(strandedness, library_type):
    """Map strandedness + library type to HISAT2 --rna-strandness flag."""
    if strandedness == "unstranded":
        return ""
    if library_type == "paired":
        return "--rna-strandness RF" if strandedness == "fr-firststrand" else "--rna-strandness FR"
    return "--rna-strandness R" if strandedness == "fr-firststrand" else "--rna-strandness F"


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
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _pair_fastqs(fastq_paths):
    """Group paired-end FASTQs by prefix. Returns [(r1, r2), ...]."""
    import re
    pairs = {}
    pattern = re.compile(r'^(.+?)(?:_R([12])|_([12]))\.(?:fq|fastq)\.gz$', re.IGNORECASE)
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
    return result


@shared_task(bind=True)
def run_core_pipeline(self, session_id, submission_id):
    """Core Pipeline router: dispatches to the correct entry point.

    Routes:
      - fastq:     Full pipeline (FastQC → Trim → HISAT2 → featureCounts → Stage 2)
      - alignment:  Skip to featureCounts on uploaded BAMs → Stage 2
      - matrix:    Skip to Stage 2 stats with user-provided count matrix
    """
    from pipeline.models import AnalysisJob, AnalysisSubmission, FileAsset

    job = AnalysisJob.objects.get(job_id=self.request.id)
    job.status = AnalysisJob.Status.RUNNING
    job.save(update_fields=["status"])

    try:
        submission = AnalysisSubmission.objects.get(submission_id=submission_id)
        input_type = submission.input_data_type

        if input_type == "fastq":
            result = _route_fastq(submission)
        elif input_type == "alignment":
            result = _route_alignment(submission)
        elif input_type == "matrix":
            result = _route_matrix(submission)
        else:
            raise ValueError(f"Unknown input_data_type: {input_type}")

        job.status = AnalysisJob.Status.SUCCESS
        job.result_payload = {"message": "Core pipeline completed.", **result}
        job.save(update_fields=["status", "result_payload"])

    except Exception as exc:
        logger.exception("Core pipeline failed for submission %s", submission_id)
        job.status = AnalysisJob.Status.FAILED
        job.result_payload = {"error": str(exc)}
        job.save(update_fields=["status", "result_payload"])
        raise

    return {"job_id": str(job.job_id), "status": job.status}


def _route_fastq(submission):
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

    # Resolve genome paths
    genome_key = submission.reference_genome
    hisat2_idx, genome_fasta, genome_gtf = _resolve_genome(
        genome_key, work_dir, build_hisat2=True
    )

    # ─── Step 1: FastQC on raw FASTQs ───
    _run(f"fastqc -o {qc_dir} -t 8 {' '.join(fastq_assets)}")

    # ─── Step 2: Trimmomatic ───
    trimmed_files = []
    if library_type == "paired":
        pairs = _pair_fastqs(fastq_assets)
        for r1, r2 in pairs:
            prefix = os.path.basename(r1).split("_R1")[0].split("_1.")[0]
            out_r1 = os.path.join(trimmed_dir, f"{prefix}_R1_trimmed.fq.gz")
            out_r1_unpaired = os.path.join(trimmed_dir, f"{prefix}_R1_unpaired.fq.gz")
            out_r2 = os.path.join(trimmed_dir, f"{prefix}_R2_trimmed.fq.gz")
            out_r2_unpaired = os.path.join(trimmed_dir, f"{prefix}_R2_unpaired.fq.gz")

            _run(
                f"trimmomatic PE -threads 8 "
                f"{r1} {r2} "
                f"{out_r1} {out_r1_unpaired} "
                f"{out_r2} {out_r2_unpaired} "
                f"ILLUMINACLIP:TruSeq3-PE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
            )
            trimmed_files.append((out_r1, out_r2, prefix))
    else:
        for fq in fastq_assets:
            name = os.path.basename(fq)
            stem = name.replace(".fq.gz", "").replace(".fastq.gz", "")
            out = os.path.join(trimmed_dir, f"{stem}_trimmed.fq.gz")

            _run(
                f"trimmomatic SE -threads 8 "
                f"{fq} {out} "
                f"ILLUMINACLIP:TruSeq3-SE.fa:2:30:10 "
                f"LEADING:3 TRAILING:3 SLIDINGWINDOW:4:15 MINLEN:36"
            )
            trimmed_files.append((out, stem))

    # ─── Step 3: HISAT2 → samtools BAM ───
    strand_flag = _strandedness_hisat2(strandedness, library_type)
    bam_files = []

    if library_type == "paired":
        for r1, r2, prefix in trimmed_files:
            bam_path = os.path.join(aligned_dir, f"{prefix}.bam")
            _run(
                f"hisat2 -p 8 --dta {strand_flag} "
                f"-x {hisat2_idx} -1 {r1} -2 {r2} "
                f"| samtools view -b -@ 4 "
                f"| samtools sort -@ 4 -o {bam_path}"
            )
            _run(f"samtools index {bam_path}")
            bam_files.append(bam_path)
    else:
        for fq, stem in trimmed_files:
            bam_path = os.path.join(aligned_dir, f"{stem}.bam")
            _run(
                f"hisat2 -p 8 --dta {strand_flag} "
                f"-x {hisat2_idx} -U {fq} "
                f"| samtools view -b -@ 4 "
                f"| samtools sort -@ 4 -o {bam_path}"
            )
            _run(f"samtools index {bam_path}")
            bam_files.append(bam_path)

    # ─── Step 4: featureCounts ───
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

    # ─── Step 5: MultiQC ───
    _run(f"multiqc {work_dir} -o {qc_dir} --force --no-data-dir")

    # ─── Stage 2: Statistical analysis ───
    stats_result = run_stage2_stats(submission)

    return {
        "count_matrix": count_matrix_path,
        "qc_dir": qc_dir,
        **stats_result,
    }


def _route_alignment(submission):
    """Route B: Start from uploaded BAM/CRAM files → featureCounts → Stage 2."""
    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    qc_dir = os.path.join(work_dir, "qc")
    os.makedirs(counts_dir, exist_ok=True)
    os.makedirs(qc_dir, exist_ok=True)

    bam_assets = list(
        submission.file_assets.filter(
            file_role=FileAsset.FileRole.ALIGNMENT_BAM
        ).values_list("local_path", flat=True)
    )

    strandedness = submission.strandedness or "unstranded"
    quant_level = submission.metadata_payload.get("quant_level", "gene")
    library_type = submission.library_type or "single"

    # Resolve genome (only GTF needed for featureCounts)
    genome_key = submission.reference_genome
    _, _, genome_gtf = _resolve_genome(
        genome_key, work_dir, build_hisat2=False
    )

    # Convert CRAM to BAM if needed (featureCounts requires BAM)
    bam_files = []
    aligned_dir = os.path.join(work_dir, "aligned")
    os.makedirs(aligned_dir, exist_ok=True)
    for path in bam_assets:
        if path.endswith(".cram"):
            bam_path = os.path.join(
                aligned_dir, os.path.basename(path).replace(".cram", ".bam")
            )
            _run(f"samtools view -b -@ 4 -o {bam_path} {path}")
            _run(f"samtools index {bam_path}")
            bam_files.append(bam_path)
        else:
            # Index if .bai doesn't exist
            if not os.path.exists(path + ".bai"):
                _run(f"samtools index {path}")
            bam_files.append(path)

    # featureCounts
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

    # Stage 2
    stats_result = run_stage2_stats(submission)

    return {"count_matrix": count_matrix_path, "qc_dir": qc_dir, **stats_result}


def _route_matrix(submission):
    """Route C: Start from user-provided count matrix → Stage 2 stats only."""
    import pandas as pd

    from pipeline.models import FileAsset
    from pipeline.stats import run_stage2_stats

    work_dir = submission.upload_dir
    counts_dir = os.path.join(work_dir, "counts")
    os.makedirs(counts_dir, exist_ok=True)

    # Find the uploaded count matrix
    matrix_asset = submission.file_assets.filter(
        file_role=FileAsset.FileRole.USER_COUNT_MATRIX
    ).first()
    if not matrix_asset:
        raise RuntimeError("No count matrix file found.")

    user_path = matrix_asset.local_path

    # Detect separator and load
    sep = "\t" if user_path.endswith(".tsv") else ","
    df = pd.read_csv(user_path, sep=sep, index_col=0)

    # Validate: all values should be non-negative integers (raw counts)
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise ValueError("Count matrix is empty.")
    if not all(df.dtypes.apply(lambda dt: pd.api.types.is_numeric_dtype(dt))):
        raise ValueError(
            "Count matrix contains non-numeric columns. "
            "Ensure all sample columns contain raw integer counts."
        )
    if (df < 0).any().any():
        raise ValueError("Count matrix contains negative values.")

    # Copy to canonical location for Stage 2
    canonical_path = os.path.join(counts_dir, "raw_counts.csv")
    df.to_csv(canonical_path)

    # Register as pipeline-generated count matrix too
    FileAsset.objects.create(
        session_id=submission.session_id,
        submission=submission,
        file_role=FileAsset.FileRole.COUNT_MATRIX,
        local_path=canonical_path,
        is_user_uploaded=False,
    )

    # Stage 2
    stats_result = run_stage2_stats(submission)

    return {"count_matrix": canonical_path, **stats_result}


def _resolve_genome(genome_key, work_dir, build_hisat2=True):
    """Resolve genome paths. Returns (hisat2_idx, fasta, gtf).

    For custom genomes, optionally build HISAT2 index.
    For alignment entry, only GTF is strictly needed.
    """
    if genome_key == "custom":
        custom_dir = os.path.join(work_dir, "custom_genome")
        fasta_files = glob.glob(os.path.join(custom_dir, "*.fa")) + \
                      glob.glob(os.path.join(custom_dir, "*.fasta")) + \
                      glob.glob(os.path.join(custom_dir, "*.fna"))
        gtf_files = glob.glob(os.path.join(custom_dir, "*.gtf")) + \
                    glob.glob(os.path.join(custom_dir, "*.gff")) + \
                    glob.glob(os.path.join(custom_dir, "*.gff3"))
        genome_fasta = fasta_files[0] if fasta_files else None
        genome_gtf = gtf_files[0] if gtf_files else None

        hisat2_idx = None
        if build_hisat2 and genome_fasta:
            idx_prefix = os.path.join(custom_dir, "hisat2_index")
            os.makedirs(os.path.dirname(idx_prefix) or custom_dir, exist_ok=True)
            _run(f"hisat2-build -p 8 {genome_fasta} {idx_prefix}")
            hisat2_idx = idx_prefix

        return hisat2_idx, genome_fasta, genome_gtf
    else:
        return _genome_paths(genome_key)


def _run_featurecounts(bam_files, genome_gtf, strandedness, quant_level,
                       library_type, work_dir):
    """Run featureCounts and convert output to clean CSV. Returns CSV path."""
    counts_dir = os.path.join(work_dir, "counts")
    os.makedirs(counts_dir, exist_ok=True)
    count_matrix_path = os.path.join(counts_dir, "raw_counts.csv")
    fc_output = os.path.join(counts_dir, "featurecounts_output.txt")
    s_flag = _strandedness_fc(strandedness)
    t_flag = _feature_type(quant_level)
    paired_flag = "-p --countReadPairs" if library_type == "paired" else ""

    _run(
        f"featureCounts {paired_flag} "
        f"-T 8 -s {s_flag} -t {t_flag} -g gene_id "
        f"-a {genome_gtf} "
        f"-o {fc_output} "
        f"{' '.join(bam_files)}"
    )

    _featurecounts_to_csv(fc_output, count_matrix_path)
    return count_matrix_path


def _featurecounts_to_csv(fc_path, csv_path):
    """Convert featureCounts tab-delimited output to a clean CSV.

    featureCounts output has a comment header (starting with #),
    followed by a header line: Geneid, Chr, Start, End, Strand, Length, then sample columns.
    We extract Geneid + sample counts only.
    """
    with open(fc_path) as fin:
        lines = [line for line in fin if not line.startswith("#")]

    if not lines:
        raise RuntimeError("featureCounts output is empty.")

    header = lines[0].strip().split("\t")
    # Columns 0=Geneid, 1-5=Chr/Start/End/Strand/Length, 6+=samples
    sample_cols = header[6:]
    # Clean sample column names (featureCounts uses full paths)
    clean_names = [os.path.basename(c).replace(".bam", "").replace(".cram", "") for c in sample_cols]

    with open(csv_path, "w", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["gene_id"] + clean_names)
        for line in lines[1:]:
            fields = line.strip().split("\t")
            writer.writerow([fields[0]] + fields[6:])
