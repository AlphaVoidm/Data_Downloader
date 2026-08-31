"""Post-extraction validation helpers (acquisition, not ML preprocessing).

    * coverage     — did the retrieved grid/period match what was requested?
    * completeness — are the expected months present?
    * units        — does the reported unit match the feature's known unit?
"""
from .coverage import check_spatial_coverage, check_temporal_coverage
from .completeness import completeness_ratio, missing_months
from .units import KNOWN_UNITS, unit_matches

__all__ = [
    "check_spatial_coverage", "check_temporal_coverage",
    "completeness_ratio", "missing_months",
    "KNOWN_UNITS", "unit_matches",
]
