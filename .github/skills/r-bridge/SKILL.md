---
description: "USE when writing R code via rpy2, calling R packages from Python, converting data between R and Python, or debugging R bridge errors."
---

# Skill: rpy2 R Bridge

How to call R from Python in the RNAseek codebase using the shared rpy2 bridge.

## Setup

All R bridge infrastructure is in `pipeline/stats/_r_bridge.py`. Never initialize rpy2 directly.

```python
from pipeline.stats._r_bridge import ro, importr, _converter, localconverter, _R_CORES
```

### What the bridge provides

| Symbol | Purpose |
|---|---|
| `ro` | `rpy2.robjects` — main R interface |
| `importr` | Load an R package: `deseq2 = importr("DESeq2")` |
| `_converter` | Combined numpy + pandas converter for automatic type coercion |
| `localconverter` | Context manager to activate the converter |
| `_R_CORES` | CPU count for R parallelism: `max(2, os.cpu_count() // 2)` |

## Pattern: Call an R function

```python
from pipeline.stats._r_bridge import ro, importr, _converter, localconverter, _R_CORES

def _run_some_r_analysis(counts_df, metadata_df):
    with localconverter(_converter):
        r_pkg = importr("PackageName")
        # pandas DataFrames auto-convert to R data.frames inside this context
        result = r_pkg.some_function(counts_df, metadata_df)
        # R data.frames auto-convert back to pandas
        return result
```

## Pattern: BiocParallel for R parallelism

```python
from pipeline.stats._r_bridge import importr, _R_CORES

bioc_parallel = importr("BiocParallel")
bp_param = bioc_parallel.MulticoreParam(workers=_R_CORES)
# Pass bp_param to Bioconductor functions that accept BPPARAM
```

## Pattern: Suppress R warnings in Celery workers

The bridge already suppresses the "R is not initialized by the main thread" warning. No action needed. If an R package emits noisy warnings, wrap with:

```python
import warnings
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=UserWarning)
    result = r_pkg.noisy_function(args)
```

## Available R packages (via conda)

Installed in `environment.yml`:
- `r-base=4.3`
- `bioconductor-deseq2` — differential expression
- `bioconductor-sva` — ComBat batch correction
- `bioconductor-dexseq` — differential exon usage
- `bioconductor-isoformswitchanalyzer` — alternative splicing
- `bioconductor-tcgabiolinks` — TCGA data access
- `bioconductor-mixomics` — multi-omics integration (DIABLO)
- `r-wgcna` — weighted gene co-expression networks

## Common pitfalls

1. **Always use `localconverter(_converter)`** — Without it, pandas DataFrames will NOT auto-convert to R data.frames. You will get cryptic `rpy2.rinterface_lib.sexp.NACharacterType` errors.
2. **R packages must be installed via conda** — Listed in `environment.yml`, not `requirements.txt`. If a module needs a new R package, add it there.
3. **Thread safety** — rpy2 is NOT thread-safe. Do not call R from multiple threads simultaneously. Use `_R_CORES` for R-internal parallelism only (BiocParallel), not Python threads.
4. **Integer overflow** — R uses 32-bit integers. For large count matrices (>2B reads), convert to float first.
