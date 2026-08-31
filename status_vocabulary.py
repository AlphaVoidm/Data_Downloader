"""Unified per-source status vocabulary (acquisition/fallback reporting).

The downloader distinguishes WHY a given source was selected or skipped for a
country x feature pair, so a country is never marked unavailable merely because
ONE source did not cover it:

    SOURCE_SUCCESS                 data retrieved + validated
    SOURCE_NOT_COVERED             source does not publish this country/feature
    SOURCE_AUTH_REQUIRED           data exists but a credential/subscription is needed
    SOURCE_TEMPORARILY_UNAVAILABLE known outage / no period overlap / mapping missing
    SOURCE_RATE_LIMITED            throttled (429)
    SOURCE_FORMAT_ERROR            wrong Content-Type / HTML portal / bad XML/JSON/CSV / schema
    SOURCE_API_ERROR               network / timeout / server error
    SOURCE_DATA_EMPTY              endpoint OK but zero records returned
"""
from __future__ import annotations

SOURCE_SUCCESS = "SOURCE_SUCCESS"
SOURCE_NOT_COVERED = "SOURCE_NOT_COVERED"
SOURCE_AUTH_REQUIRED = "SOURCE_AUTH_REQUIRED"
SOURCE_TEMPORARILY_UNAVAILABLE = "SOURCE_TEMPORARILY_UNAVAILABLE"
SOURCE_RATE_LIMITED = "SOURCE_RATE_LIMITED"
SOURCE_FORMAT_ERROR = "SOURCE_FORMAT_ERROR"
SOURCE_API_ERROR = "SOURCE_API_ERROR"
SOURCE_DATA_EMPTY = "SOURCE_DATA_EMPTY"

ALL_SOURCE_STATUSES = (
    SOURCE_SUCCESS,
    SOURCE_NOT_COVERED,
    SOURCE_AUTH_REQUIRED,
    SOURCE_TEMPORARILY_UNAVAILABLE,
    SOURCE_RATE_LIMITED,
    SOURCE_FORMAT_ERROR,
    SOURCE_API_ERROR,
    SOURCE_DATA_EMPTY,
)


def source_status(status: str) -> str:
    """Map any discovery / verification / acquisition status onto the SOURCE_* vocabulary."""
    s = (status or "").strip()

    # already in the vocabulary
    if s in ALL_SOURCE_STATUSES:
        return s

    # success / verified / discovery-supported
    if s in ("SUCCESS", "PARTIAL_SUCCESS", "VERIFIED", "OK", "SUPPORTED"):
        return SOURCE_SUCCESS

    # not covered
    if s in ("NOT_SUPPORTED", "UNKNOWN"):
        return SOURCE_NOT_COVERED

    # auth
    if s in ("AUTH_REQUIRED", "AUTH_FAILED"):
        return SOURCE_AUTH_REQUIRED

    # temporarily unavailable (includes pending area/series mapping)
    if s in ("TEMPORARILY_UNAVAILABLE", "MAPPING_REQUIRED", "BULK_MANUAL"):
        return SOURCE_TEMPORARILY_UNAVAILABLE

    # rate limited
    if s == "RATE_LIMITED":
        return SOURCE_RATE_LIMITED

    # format / content problems
    if s in ("PORTAL_HTML", "NON_DATA_RESPONSE", "INVALID_XML", "INVALID_JSON",
             "INVALID_CSV", "SCHEMA_MISMATCH", "PARSE_ERROR"):
        return SOURCE_FORMAT_ERROR

    # empty data
    if s in ("EMPTY_RESPONSE", "NO_RECORDS"):
        return SOURCE_DATA_EMPTY

    # network / server errors
    if s in ("NETWORK_ERROR", "TIMEOUT", "NOT_VERIFIED", "DEPENDENCY_MISSING", "DOWNLOAD_ERROR"):
        return SOURCE_API_ERROR

    return SOURCE_API_ERROR


__all__ = [
    "SOURCE_SUCCESS", "SOURCE_NOT_COVERED", "SOURCE_AUTH_REQUIRED",
    "SOURCE_TEMPORARILY_UNAVAILABLE", "SOURCE_RATE_LIMITED",
    "SOURCE_FORMAT_ERROR", "SOURCE_API_ERROR", "SOURCE_DATA_EMPTY",
    "ALL_SOURCE_STATUSES", "source_status",
]
