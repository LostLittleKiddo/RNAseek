"""R bridge — initialise rpy2 with suppressed warnings and shared converter."""

import os
import warnings

import numpy as np

# Number of cores available for R parallelism
_R_CORES = max(2, (os.cpu_count() or 4) // 2)

# rpy2 emits a UserWarning about SIGINT when R is initialized from a non-main
# thread (e.g. a Celery worker).  The warning is harmless.
with warnings.catch_warnings():
    warnings.filterwarnings(
        "ignore",
        message="R is not initialized by the main thread",
        category=UserWarning,
    )
    import rpy2.robjects as ro
    from rpy2.robjects import numpy2ri, pandas2ri
    from rpy2.robjects.conversion import localconverter
    from rpy2.robjects.packages import importr

# Combined converter for numpy + pandas <-> R
_converter = ro.default_converter + numpy2ri.converter + pandas2ri.converter
