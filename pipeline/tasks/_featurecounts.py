"""featureCounts execution and output conversion."""

import csv
import logging
import os

from pipeline.tasks._constants import _CPU_COUNT
from pipeline.tasks._helpers import _feature_type, _q, _run, _strandedness_fc

logger = logging.getLogger(__name__)


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

    # Detect annotation format: GFF/GFF3 files need -F GFF
    annotation_ext = (
        os.path.splitext(genome_gtf)[1].lower() if genome_gtf else ".gtf"
    )
    if annotation_ext in (".gff", ".gff3"):
        format_flag = "-F GFF"
        g_attr = _detect_gff_gene_attr(genome_gtf)
    else:
        format_flag = ""
        g_attr = "gene_id"

    bams_str = " ".join(_q(b) for b in bam_files)
    _run(
        f"featureCounts {paired_flag} "
        f"-T {_CPU_COUNT} -s {s_flag} -t {t_flag} -g {g_attr} "
        f"{format_flag} "
        f"-a {_q(genome_gtf)} "
        f"-o {_q(fc_output)} "
        f"{bams_str}"
    )

    _featurecounts_to_csv(fc_output, count_matrix_path)
    return count_matrix_path


def _detect_gff_gene_attr(gff_path):
    """Peek at a GFF/GFF3 file to determine the gene ID attribute name.

    Checks the first 200 feature lines for 'gene_id' in the attributes column.
    Falls back to 'ID' (standard GFF3) if 'gene_id' is not found.
    """
    checked = 0
    with open(gff_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            if checked >= 200:
                break
            checked += 1
            attrs = line.rstrip().split("\t")[-1] if "\t" in line else ""
            if "gene_id" in attrs:
                return "gene_id"
    return "ID"


def _featurecounts_to_csv(fc_path, csv_path):
    """Convert featureCounts tab-delimited output to a clean CSV.

    featureCounts output has a comment header (starting with #),
    followed by a header line: Geneid, Chr, Start, End, Strand, Length,
    then sample columns.  We extract Geneid + sample counts only.
    """
    with open(fc_path) as fin:
        lines = [line for line in fin if not line.startswith("#")]

    if not lines:
        raise RuntimeError("featureCounts output is empty.")

    header = lines[0].strip().split("\t")
    # Columns 0=Geneid, 1-5=Chr/Start/End/Strand/Length, 6+=samples
    sample_cols = header[6:]
    # Clean sample column names (featureCounts uses full paths)
    clean_names = [
        os.path.basename(c).replace(".bam", "").replace(".cram", "")
        for c in sample_cols
    ]

    with open(csv_path, "w", newline="") as fout:
        writer = csv.writer(fout)
        writer.writerow(["gene_id"] + clean_names)
        for line in lines[1:]:
            fields = line.strip().split("\t")
            if len(fields) > 6:
                writer.writerow([fields[0]] + fields[6:])
