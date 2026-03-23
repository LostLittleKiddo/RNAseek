"""
Tests for genome index resolution — verifies all pre-built indices exist and
that the resolver functions in _genome.py can locate them.

Covers:
- HISAT2 index resolution for all 11 genomes (_genome_paths)
- miRBase Bowtie index resolution for all 8 species (_resolve_mirbase)
- BWA index presence for all 11 genomes (_resolve_bwa_index)
- Bismark Bisulfite_Genome presence for all 11 genomes (_resolve_bismark_genome)
- _genome_dir helper
"""
import os

from django.test import TestCase

from pipeline.tasks._constants import (
    _GENOME_BASE,
    _GENOME_FOLDER_MAP,
    _MACS2_GENOME_SIZE,
    _MIRBASE_SPECIES_MAP,
)
from pipeline.tasks._genome import (
    _genome_dir,
    _genome_paths,
    _resolve_bismark_genome,
    _resolve_bwa_index,
    _resolve_mirbase,
)


class HISAT2IndexTest(TestCase):
    """Every pre-built genome must have a valid HISAT2 index, FASTA, and GTF."""

    def test_genome_base_exists(self):
        self.assertTrue(
            os.path.isdir(_GENOME_BASE),
            f"reference_genomes/ not found at {_GENOME_BASE}",
        )

    def test_all_genome_folders_exist(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            path = os.path.join(_GENOME_BASE, folder)
            self.assertTrue(os.path.isdir(path), f"Missing folder for {key}: {path}")

    def test_categorised_subdirs_exist(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            base = os.path.join(_GENOME_BASE, folder)
            for subdir in ("genome", "hisat2_index", "bwa_index", "bismark_index"):
                path = os.path.join(base, subdir)
                self.assertTrue(
                    os.path.isdir(path),
                    f"Missing {subdir}/ in {base}",
                )

    def test_hisat2_index_resolved_for_all_genomes(self):
        for key in _GENOME_FOLDER_MAP:
            idx, fasta, gtf = _genome_paths(key)
            self.assertIsNotNone(idx, f"No HISAT2 index for {key}")
            self.assertTrue(
                os.path.isfile(f"{idx}.1.ht2"),
                f"HISAT2 index file missing: {idx}.1.ht2",
            )

    def test_fasta_found_for_all_genomes(self):
        for key in _GENOME_FOLDER_MAP:
            _, fasta, _ = _genome_paths(key)
            self.assertIsNotNone(fasta, f"No FASTA for {key}")
            self.assertTrue(os.path.isfile(fasta), f"FASTA missing: {fasta}")

    def test_gtf_found_for_all_genomes(self):
        for key in _GENOME_FOLDER_MAP:
            _, _, gtf = _genome_paths(key)
            self.assertIsNotNone(gtf, f"No GTF for {key}")
            self.assertTrue(os.path.isfile(gtf), f"GTF missing: {gtf}")

    def test_genome_dir_helper(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            result = _genome_dir(key)
            expected = os.path.join(_GENOME_BASE, folder)
            self.assertEqual(result, expected)


class MiRBaseIndexTest(TestCase):
    """Every miRBase species must have a pre-built Bowtie index."""

    def test_mirbase_dir_exists(self):
        mirbase_dir = os.path.join(_GENOME_BASE, "miRBase")
        self.assertTrue(
            os.path.isdir(mirbase_dir),
            f"miRBase/ not found at {mirbase_dir}",
        )

    def test_bowtie_index_resolved_for_all_species(self):
        for genome_key, species in _MIRBASE_SPECIES_MAP.items():
            idx_prefix = _resolve_mirbase(genome_key)
            self.assertIsNotNone(idx_prefix, f"No miRBase index for {genome_key}")
            self.assertTrue(
                os.path.isfile(f"{idx_prefix}.1.ebwt"),
                f"Bowtie index missing: {idx_prefix}.1.ebwt",
            )

    def test_species_fa_exists(self):
        mirbase_dir = os.path.join(_GENOME_BASE, "miRBase")
        for species in set(_MIRBASE_SPECIES_MAP.values()):
            fa = os.path.join(mirbase_dir, species, f"{species}_mature.fa")
            self.assertTrue(os.path.isfile(fa), f"Species FASTA missing: {fa}")


class BWAIndexTest(TestCase):
    """Every pre-built genome must have a BWA index in bwa_index/ subdirectory."""

    def test_bwa_index_dir_exists_for_all_genomes(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            bwa_dir = os.path.join(_GENOME_BASE, folder, "bwa_index")
            self.assertTrue(
                os.path.isdir(bwa_dir),
                f"bwa_index/ missing for {key}: {bwa_dir}",
            )

    def test_resolve_bwa_index_succeeds(self):
        for key in _GENOME_FOLDER_MAP:
            result = _resolve_bwa_index(genome_key=key)
            self.assertIsNotNone(result, f"No BWA FASTA for {key}")
            bwt = result + ".bwt"
            self.assertTrue(os.path.isfile(bwt), f"BWA index missing: {bwt}")


class BismarkIndexTest(TestCase):
    """Every pre-built genome must have Bismark's Bisulfite_Genome/ in bismark_index/."""

    def test_bisulfite_genome_exists_for_all_genomes(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            bismark_dir = os.path.join(_GENOME_BASE, folder, "bismark_index")
            bisulfite_dir = os.path.join(bismark_dir, "Bisulfite_Genome")
            self.assertTrue(
                os.path.isdir(bisulfite_dir),
                f"Bisulfite_Genome/ missing in {bismark_dir}",
            )

    def test_resolve_bismark_genome_succeeds(self):
        for key, folder in _GENOME_FOLDER_MAP.items():
            result = _resolve_bismark_genome(genome_key=key)
            expected = os.path.join(_GENOME_BASE, folder, "bismark_index")
            self.assertEqual(result, expected)


class MACS2GenomeSizeTest(TestCase):
    """MACS2 effective genome size is defined for every genome with ChIP-seq support."""

    def test_all_genomes_have_macs2_size(self):
        for key in _GENOME_FOLDER_MAP:
            self.assertIn(
                key, _MACS2_GENOME_SIZE,
                f"No MACS2 genome size for {key}",
            )
