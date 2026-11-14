"""Backlink delivery loop - a local, offline, standard-library-only toolkit.

This package prepares and records outreach work. It never sends mail and never
submits a form: transmission stays behind a human review gate (R-520 / R-530 tier B).
"""

__version__ = "0.2.0"

# Contract C. Version 1.1.0 adds the optional discovery block (submission_url,
# form_shape, form_fields, contact_email, contact_source, contact_source_url,
# enriched_at). The producer emits the newest version; the consumer accepts every
# version it knows, and rejects a discovery field smuggled into a 1.0.0 document.
CONTRACTS_VERSION = "1.1.0"
SUPPORTED_CONTRACT_VERSIONS = ("1.0.0", "1.1.0")
# Fields that only exist from 1.1.0 onward.
DISCOVERY_FIELDS = (
    "submission_url",
    "form_shape",
    "form_fields",
    "contact_email",
    "contact_source",
    "contact_source_url",
    "enriched_at",
)
