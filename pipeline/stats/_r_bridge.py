"""R bridge — initialise rpy2 with suppressed warnings and shared converter.

When rpy2 is not installed (e.g. during Django startup for migrations
without the full conda environment), all symbols are set to None so the
web process can still import models and views. The pipeline worker that
actually calls R-based functions must have rpy2 + R installed.
"""

import os
import warnings

import numpy as np

# Number of cores available for R parallelism
_R_CORES = max(2, (os.cpu_count() or 4) // 2)

try:
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

except ImportError:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "rpy2 is not installed — R-based pipeline steps will be unavailable."
    )
    ro = None  # type: ignore[assignment]
    localconverter = None  # type: ignore[assignment]
    importr = None  # type: ignore[assignment]
    _converter = None  # type: ignore[assignment]
