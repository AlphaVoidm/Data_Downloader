"""Unit sanity checks for acquired features.

Only flags *known-wrong* units; it never imputes or converts values — unit
conversion at extraction time (K -> °C, m -> mm) is done by the connector spec.
"""
from __future__ import annotations

KNOWN_UNITS: dict[str, set[str]] = {
    "temperature_2m": {"°C", "degC", "celsius", "K", "kelvin"},
    "solar_radiation": {"kWh/m²/day", "kwh/m2/day", "W/m²", "w/m2", "J/m²"},
    "wind_speed_10m": {"m/s", "m s-1", "ms-1", "km/h"},
    "precipitation": {"mm", "mm/day", "mm/month", "m"},
    "electricity_demand": {"TWh", "GWh", "MWh", "MW"},
    "total_electricity_generation": {"TWh", "GWh", "MWh", "kWh"},
    "renewable_generation_share": {"%", "percent", "share"},
    "generation_mix": {"TWh", "GWh"},
    "gdp": {"current USD", "USD", "constant USD"},
    "gdp_growth": {"%"},
    "total_population": {"count", "persons"},
    "public_holidays": {"count", "calendar_flag"},
}


def unit_matches(concept: str, unit: str) -> tuple[bool, str]:
    """Return (ok, note) for a feature's reported unit."""
    known = KNOWN_UNITS.get(concept)
    if not known:
        return True, "no unit contract registered for this feature"
    if not unit:
        return False, "unit not reported"
    if any(u in unit for u in known):
        return True, f"unit '{unit}' recognized"
    return False, f"unit '{unit}' not in expected set {sorted(known)}"


__all__ = ["KNOWN_UNITS", "unit_matches"]
