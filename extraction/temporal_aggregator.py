"""Temporal aggregation helpers (data extraction, not ML preprocessing).

    * ``to_monthly_series`` — resample a daily dataframe to month-end with a
      configured aggregation per column.
    * ``derive_degree_days`` — CDD/HDD from daily temperature (base 18 °C).
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def to_monthly_series(
    daily: pd.DataFrame,
    date_col: str = "date",
    aggregations: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Resample a daily frame to month-end timestamps.

    ``aggregations`` maps column -> "mean" | "sum". Columns not listed default
    to "mean". The date column becomes the index (month-end).
    """
    df = daily.copy()
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col)
    aggs: dict[str, Any] = {
        col: (agg if agg in ("mean", "sum") else "mean")
        for col, agg in (aggregations or {}).items()
    }
    for col in df.columns:
        aggs.setdefault(col, "mean")
    monthly = df.resample("ME").agg(aggs)
    monthly.index.name = "date"
    return monthly.reset_index()


def derive_degree_days(daily: pd.DataFrame, temp_col: str = "t2m", base: float = 18.0) -> pd.DataFrame:
    """Derive daily CDD/HDD columns from a daily temperature column.

    Returns the frame with two extra columns: ``cdd`` and ``hdd``.
    """
    df = daily.copy()
    temp = pd.to_numeric(df[temp_col], errors="coerce")
    df["cdd"] = (temp - base).clip(lower=0.0)
    df["hdd"] = (base - temp).clip(lower=0.0)
    return df


__all__ = ["to_monthly_series", "derive_degree_days"]
