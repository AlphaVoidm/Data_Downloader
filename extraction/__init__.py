"""Extraction layer: CDS retrieval + temporal aggregation.

Houses the gridded-climate extraction engine (chunked CDS bbox requests) and
the temporal aggregation helpers (daily -> monthly, degree-day derivation).
"""
from .cds_extractor import (
    AUTH_FAILED,
    NETWORK_ERROR,
    TERMS_NOT_ACCEPTED,
    TIMEOUT,
    classify_cds_error,
    cds_credentials_available,
    make_cds_client,
    extract_monthly_chunked,
    iter_year_chunks,
)
from .temporal_aggregator import (
    derive_degree_days,
    to_monthly_series,
)

__all__ = [
    "AUTH_FAILED", "NETWORK_ERROR", "TERMS_NOT_ACCEPTED", "TIMEOUT",
    "classify_cds_error", "cds_credentials_available", "make_cds_client",
    "extract_monthly_chunked", "iter_year_chunks",
    "derive_degree_days", "to_monthly_series",
]
