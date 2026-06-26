"""First-run setup + onboarding domain.

The primitives behind ``/api/setup/*`` AND the ``zotero-summarizer setup`` CLI —
one implementation, two front-ends. See README.md for the flow.
"""
from zotero_summarizer.services.setup.bootstrap import bootstrap_phase0
from zotero_summarizer.services.setup.detect import detect_zotero_data_dirs
from zotero_summarizer.services.setup.env_writer import write_env_paths
from zotero_summarizer.services.setup.status import get_setup_status
from zotero_summarizer.services.setup.validate import validate_config_draft

__all__ = [
    "bootstrap_phase0",
    "detect_zotero_data_dirs",
    "get_setup_status",
    "validate_config_draft",
    "write_env_paths",
]
