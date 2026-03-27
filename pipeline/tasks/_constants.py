"""Pipeline constants: genome maps, CPU settings, tool configurations."""

import os

# ── Parallelism settings ──
_CPU_COUNT = os.cpu_count() or 4
# Threads per individual tool invocation (HISAT2, samtools, etc.)
# Configurable via env var for asymmetric worker pools:
#   - celery-cpu.service sets RNASEEK_TOOL_THREADS=8  (8 threads × 5 workers = 40 cores)
#   - celery-ram.service leaves the default (R tasks don't use _TOOL_THREADS)
#   - Development: falls back to max(4, CPU_COUNT // 2)
_TOOL_THREADS = int(os.environ.get(
    "RNASEEK_TOOL_THREADS", str(max(4, _CPU_COUNT // 2))
))
# Max parallel samples to process simultaneously
_PARALLEL_SAMPLES = int(os.environ.get(
    "RNASEEK_PARALLEL_SAMPLES", str(max(2, _CPU_COUNT // _TOOL_THREADS))
))

# ── Paths to pre-indexed reference genomes ──
_GENOME_BASE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "reference_genomes"
)

# Mapping: genome_key (from frontend) → folder name under reference_genomes/
_GENOME_FOLDER_MAP = {
    "hg38":      "Human_GRCh38",
    "mm39":      "Mouse_GRCm39",
    "mm10":      "Mouse_GRCm38",
    "rn7":       "Rat_rn7",
    "danRer11":  "Zebrafish_GRCz11",
    "galGal6":   "Chicken_GRCg6a",
    "susScr11":  "Pig_Sscrofa11.1",
    "dm6":       "Drosophila_dm6",
    "wbcel235":  "Celegans_WBcel235",
    "r64":       "Yeast_sacCer3",
    "araTha":    "Arabidopsis_TAIR10",
}

# miRBase three-letter species codes for Small RNA (Track B)
_MIRBASE_SPECIES_MAP = {
    "hg38": "hsa", "mm39": "mmu", "mm10": "mmu", "rn7": "rno",
    "danRer11": "dre", "galGal6": "gga", "dm6": "dme",
    "wbcel235": "cel", "araTha": "ath",
}

# MACS2 effective genome size shortcuts for ChIP-seq (Track C)
_MACS2_GENOME_SIZE = {
    "hg38": "hs", "mm39": "mm", "mm10": "mm", "rn7": "1.87e9",
    "danRer11": "1.37e9", "galGal6": "1.0e9", "dm6": "dm",
    "wbcel235": "ce", "r64": "1.2e7", "araTha": "1.19e8",
    "susScr11": "2.5e9",
}
