"""Monthly completeness checks for extracted country-level series."""
from __future__ import annotations

from typing import Any

import pandas as pd


def _monthly_index(start_year: int, end_year: int) -> pd.DatetimeIndex:
    return pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M").to_timestamp()


def missing_months(dates: Any, start_year: int, end_year: int) -> list[str]:
    """Return the expected month labels that are absent from `dates`."""
    expected = _monthly_index(start_year, end_year)
    observed = pd.DatetimeIndex(pd.to_datetime(dates)).to_period("M")
    missing = expected.to_period("M").difference(observed)
    return [str(p) for p in sorted(missing)]


def completeness_ratio(dates: Any, start_year: int, end_year: int) -> float:
    """Fraction of expected months present (0.0-1.0)."""
    expected = len(_monthly_index(start_year, end_year))
    if expected == 0:
        return 1.0
    present = expected - len(missing_months(dates, start_year, end_year))
    return round(present / expected, 4)


__all__ = ["missing_months", "completeness_ratio"]
