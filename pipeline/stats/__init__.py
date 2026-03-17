"""Pipeline stats package — re-exports the public interface."""

from pipeline.stats.core import run_stage2_stats  # noqa: F401

from pipeline.stats._helpers import (          # noqa: F401
    _align_samples,
    _combat_seq,
    _detect_outliers,
    _filter_low_counts,
    _load_metadata,
)

from pipeline.stats._deseq2 import (          # noqa: F401
    _build_formula_string,
    _r_string_vector,
    _run_deseq2,
    _sanitize_factor_levels,
)

from pipeline.stats._plots import (           # noqa: F401
    _generate_plot_data,
)

from pipeline.stats._annotations import (     # noqa: F401
    annotate_deg_table,
)

from pipeline.stats._methylkit import (        # noqa: F401
    run_differential_methylation,
)

from pipeline.stats._plots_wgcna import (     # noqa: F401
    build_module_trait_heatmap,
    build_pathway_dotplot,
)
