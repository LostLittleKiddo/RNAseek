"""R bridge — lazy rpy2 initialisation with suppressed warnings and shared converter.

R is initialised on first use (not at import time) so that processes that
never call R-based code (web server, celery beat) are not affected by
rpy2 / R lifecycle issues.

When rpy2 is not installed (e.g. during Django startup for migrations
without the full conda environment), all symbols are set to None so the
web process can still import models and views.  The pipeline worker that
actually calls R-based functions must have rpy2 + R installed.
"""

import os
import warnings

# Number of cores available for R parallelism
_R_CORES = max(2, (os.cpu_count() or 4) // 2)

# Lazy-init state
_rpy2_initialised = False
ro = None           # type: ignore[assignment]
localconverter = None  # type: ignore[assignment]
importr = None      # type: ignore[assignment]
_converter = None   # type: ignore[assignment]


def _ensure_rpy2():
    """Initialise rpy2 on first call.  No-op after the first successful init."""
    global _rpy2_initialised, ro, localconverter, importr, _converter
    if _rpy2_initialised:
        return
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="R is not initialized by the main thread",
                category=UserWarning,
            )
            import rpy2.robjects as _ro
            from rpy2.robjects import numpy2ri, pandas2ri
            from rpy2.robjects.conversion import localconverter as _lc
            from rpy2.robjects.packages import importr as _importr

        ro = _ro
        localconverter = _lc
        importr = _importr
        _converter = _ro.default_converter + numpy2ri.converter + pandas2ri.converter
    except ImportError:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "rpy2 is not installed — R-based pipeline steps will be unavailable."
        )
    _rpy2_initialised = True
