from __future__ import annotations

# macOS / OpenMP coexistence: lightgbm and torch both ship their own libomp.
# Loading a fitted LightGBM Booster (joblib.load) after torch is imported
# segfaults inside __kmp_suspend_initialize_thread. These env vars resolve the
# duplicate-runtime conflict and force single-thread predict (we batch small).
# Must be set before any import that pulls libomp transitively.
import os as _os

_os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
_os.environ.setdefault("OMP_NUM_THREADS", "1")

from zotero_summarizer.settings import Settings

__all__ = ["Settings"]
