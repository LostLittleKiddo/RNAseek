"""Pipeline tasks package — re-exports all public symbols for backward compatibility.

External code should continue to use::

    from pipeline.tasks import run_core_pipeline, _run, _pair_fastqs, ...

The implementation is split across submodules for maintainability:

    _constants.py       — Genome maps, CPU settings, tool configurations
    _helpers.py         — Shell execution, FASTQ pairing, progress tracking, shared steps
    _genome.py          — Genome resolution (pre-indexed, custom, miRBase, BWA, Bismark)
    _featurecounts.py   — featureCounts execution and output conversion
    _track_standard.py  — Track A: Standard RNA-Seq (HISAT2)
    _track_mirna.py     — Track B: Small RNA / miRNA (Bowtie)
    _track_chipseq.py   — Track C: ChIP-seq (BWA + MACS2)
    _track_methyl.py    — Track C: DNA Methylation (Bismark)
    _routes.py          — Alignment and count matrix entry points
    core.py             — Celery task entry points
"""

# ── Constants ──
from pipeline.tasks._constants import (  # noqa: F401
    _CPU_COUNT,
    _GENOME_BASE,
    _GENOME_FOLDER_MAP,
    _MACS2_GENOME_SIZE,
    _MIRBASE_SPECIES_MAP,
    _PARALLEL_SAMPLES,
    _TOOL_THREADS,
)

# ── Helpers ──
from pipeline.tasks._helpers import (  # noqa: F401
    _emit_progress,
    _feature_type,
    _pair_fastqs,
    _parse_metadata_csv,
    _q,
    _run,
    _run_fastqc_step,
    _run_multiqc_step,
    _run_trim_step,
    _sort_and_index_bam,
    _strandedness_fc,
    _strandedness_hisat2,
    _update_step,
)

# ── Genome ──
from pipeline.tasks._genome import (  # noqa: F401
    _decompress_if_needed,
    _genome_paths,
    _resolve_bismark_genome,
    _resolve_bwa_index,
    _resolve_genome,
    _resolve_mirbase,
)

# ── featureCounts ──
from pipeline.tasks._featurecounts import (  # noqa: F401
    _detect_gff_gene_attr,
    _featurecounts_to_csv,
    _run_featurecounts,
)

# ── Track routes ──
from pipeline.tasks._track_standard import _route_fastq  # noqa: F401
from pipeline.tasks._track_mirna import (  # noqa: F401
    _mirna_counts_from_bams,
    _route_small_rna,
    _run_bowtie_mirna,
)
from pipeline.tasks._track_chipseq import (  # noqa: F401
    _build_consensus_saf,
    _route_chip_seq,
    _run_bwa_align,
    _run_macs2_callpeak,
    _split_chip_samples,
)
from pipeline.tasks._track_methyl import (  # noqa: F401
    _route_methylation,
    _run_bismark_align,
    _run_bismark_extract,
)
from pipeline.tasks._routes import (  # noqa: F401
    _register_stage2_assets,
    _route_alignment,
    _route_matrix,
)

# ── WGCNA module ──
from pipeline.tasks._module_wgcna import execute_wgcna_and_pathways  # noqa: F401

# ── Celery tasks (must be importable for autodiscovery) ──
from pipeline.tasks.core import (  # noqa: F401
    purge_expired_sessions,
    run_core_pipeline,
    run_tier2_module,
)
