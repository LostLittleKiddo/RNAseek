"""Genome resolution: pre-indexed genomes, custom genomes, miRBase, BWA, Bismark."""

import glob
import logging
import os

from pipeline.tasks._constants import _GENOME_BASE, _GENOME_FOLDER_MAP, _MIRBASE_SPECIES_MAP
from pipeline.tasks._helpers import _q, _run

logger = logging.getLogger(__name__)


def _genome_paths(genome_key):
    """Return (hisat2_index_prefix, fasta_path, gtf_path) for a genome key.

    Auto-detects files inside the reference_genomes/<folder>/ directory:
      - HISAT2 index prefix: derived from *.1.ht2 files
      - GTF: first *.gtf file found
      - FASTA: first *.fa or *.fasta file found
    """
    folder_name = _GENOME_FOLDER_MAP.get(genome_key)
    if not folder_name:
        raise ValueError(f"Unknown genome key: {genome_key}")

    base = os.path.join(_GENOME_BASE, folder_name)
    if not os.path.isdir(base):
        raise FileNotFoundError(f"Genome directory not found: {base}")

    # HISAT2 index prefix: find *.1.ht2 and strip the '.1.ht2' suffix
    ht2_files = glob.glob(os.path.join(base, "*.1.ht2"))
    hisat2_idx = ht2_files[0].replace(".1.ht2", "") if ht2_files else None

    # GTF annotation
    gtf_files = glob.glob(os.path.join(base, "*.gtf"))
    genome_gtf = gtf_files[0] if gtf_files else None

    # FASTA genome
    fasta_files = (
        glob.glob(os.path.join(base, "*.fa"))
        + glob.glob(os.path.join(base, "*.fasta"))
    )
    genome_fasta = fasta_files[0] if fasta_files else None

    return hisat2_idx, genome_fasta, genome_gtf


def _resolve_genome(genome_key, work_dir, submission=None, **kwargs):
    """Resolve genome paths. Returns (hisat2_idx, fasta, gtf).

    For pre-built genomes: uses reference_genomes/ with pre-built HISAT2 index.
    For custom genomes: queries FileAsset records first, falls back to glob.
    """
    if genome_key == "custom":
        from pipeline.models import FileAsset

        genome_fasta = None
        genome_gtf = None

        # Primary: query the FileAsset model for the recorded local_path
        if submission is not None:
            fasta_asset = submission.file_assets.filter(
                file_role=FileAsset.FileRole.CUSTOM_GENOME_FASTA
            ).first()
            if fasta_asset and os.path.isfile(fasta_asset.local_path):
                genome_fasta = fasta_asset.local_path

            gtf_asset = submission.file_assets.filter(
                file_role=FileAsset.FileRole.CUSTOM_GENOME_ANNOTATION
            ).first()
            if gtf_asset and os.path.isfile(gtf_asset.local_path):
                genome_gtf = gtf_asset.local_path

        # Fallback: glob the custom_genome directory
        if not genome_fasta or not genome_gtf:
            custom_dir = os.path.join(work_dir, "custom_genome")
            if not genome_fasta:
                fasta_files = (
                    glob.glob(os.path.join(custom_dir, "*.fa"))
                    + glob.glob(os.path.join(custom_dir, "*.fasta"))
                    + glob.glob(os.path.join(custom_dir, "*.fna"))
                )
                genome_fasta = fasta_files[0] if fasta_files else None
            if not genome_gtf:
                gtf_files = (
                    glob.glob(os.path.join(custom_dir, "*.gtf"))
                    + glob.glob(os.path.join(custom_dir, "*.gff"))
                    + glob.glob(os.path.join(custom_dir, "*.gff3"))
                )
                genome_gtf = gtf_files[0] if gtf_files else None

        # Index building is handled as a tracked step in _route_fastq()
        hisat2_idx = None
        return hisat2_idx, genome_fasta, genome_gtf
    else:
        return _genome_paths(genome_key)


def _resolve_mirbase(genome_key):
    """Resolve miRBase Bowtie index prefix for small RNA alignment.

    Looks in reference_genomes/miRBase/<species_code>/ for a pre-built
    Bowtie index (*.1.ebwt). If no index exists but a FASTA is present,
    builds the Bowtie index on the fly.

    Returns the Bowtie index prefix path.
    """
    species = _MIRBASE_SPECIES_MAP.get(genome_key)
    if not species:
        raise ValueError(
            f"No miRBase reference for genome '{genome_key}'. "
            f"Supported: {list(_MIRBASE_SPECIES_MAP.keys())}"
        )

    mirbase_dir = os.path.join(_GENOME_BASE, "miRBase", species)

    # Look for pre-built Bowtie index
    ebwt_files = glob.glob(os.path.join(mirbase_dir, "*.1.ebwt"))
    if ebwt_files:
        return ebwt_files[0].replace(".1.ebwt", "")

    # No index — check for FASTA and build
    fasta_files = glob.glob(os.path.join(mirbase_dir, "*.fa"))
    if not fasta_files:
        raise FileNotFoundError(
            f"No miRBase reference found at {mirbase_dir}. "
            f"Download mature.fa from miRBase and place it there."
        )
    fasta = fasta_files[0]
    idx_prefix = os.path.join(mirbase_dir, "mirbase_idx")
    logger.info("Building Bowtie index for miRBase: %s", fasta)
    _run(f"bowtie-build {_q(fasta)} {_q(idx_prefix)}")
    return idx_prefix


def _resolve_bwa_index(genome_fasta):
    """Ensure a BWA index exists for the genome FASTA.

    BWA index files live alongside the FASTA (*.bwt, *.pac, *.ann, etc.).
    If missing, runs ``bwa index`` to build them.

    Returns the FASTA path (BWA uses it as the index prefix).
    """
    if not genome_fasta or not os.path.isfile(genome_fasta):
        raise FileNotFoundError(f"Genome FASTA not found: {genome_fasta}")

    bwt_path = genome_fasta + ".bwt"
    if not os.path.isfile(bwt_path):
        logger.info("Building BWA index for: %s", genome_fasta)
        _run(f"bwa index {_q(genome_fasta)}")

    return genome_fasta


def _resolve_bismark_genome(genome_dir):
    """Ensure Bismark genome preparation has been run.

    Bismark needs a Bisulfite_Genome/ directory inside the genome folder
    containing C→T and G→A converted indices.  Runs bismark_genome_preparation
    with --bowtie2 if not already present.

    Returns the genome directory path (Bismark's --genome argument).
    """
    if not genome_dir or not os.path.isdir(genome_dir):
        raise FileNotFoundError(f"Genome directory not found: {genome_dir}")

    bisulfite_dir = os.path.join(genome_dir, "Bisulfite_Genome")
    if not os.path.isdir(bisulfite_dir):
        logger.info("Preparing Bismark genome in: %s", genome_dir)
        _run(f"bismark_genome_preparation --bowtie2 {_q(genome_dir)}")

    return genome_dir
