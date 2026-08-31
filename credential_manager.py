"""Central credential manager for HGT-QF sources.

Single source of truth for how source credentials are named, located, merged,
and (for UI purposes only) masked. Credentials are read exclusively from:

    1. an explicit in-memory mapping (e.g. Streamlit session input)
    2. environment variables
    3. a local ``.env`` file (via python-dotenv)

Real values are NEVER printed, logged, or written to acquisition reports —
only masked previews are exposed for status displays.

Canonical credential environment variable per source_id:

    ember   -> EMBER_API_KEY
    entsoe  -> ENTSOE_API_TOKEN
    eia     -> EIA_API_KEY
    era5    -> CDS_API_KEY  (+ CDSAPI_URL, which is a URL not a secret)
"""
from __future__ import annotations

import os
import re
from typing import Any

try:  # .env loading is best-effort
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # noqa: BLE001
    pass

# source_id -> canonical environment variable name
CREDENTIAL_ENVS: dict[str, str] = {
    "ember": "EMBER_API_KEY",
    "entsoe": "ENTSOE_API_TOKEN",
    "eia": "EIA_API_KEY",
    "era5": "CDS_API_KEY",
}

# Acceptable aliases -> canonical env name (tolerates user/config naming drift).
ENV_ALIASES: dict[str, str] = {
    "ENTSOE_API_KEY": "ENTSOE_API_TOKEN",
    "CDSAPI_KEY": "CDS_API_KEY",
    "EMBER_KEY": "EMBER_API_KEY",
    "EIA_KEY": "EIA_API_KEY",
    "ENTSOE_TOKEN": "ENTSOE_API_TOKEN",
    "CDS_KEY": "CDS_API_KEY",
}

# Canonical env name -> source_id (reverse lookup for UI display).
ENV_TO_SOURCE: dict[str, str] = {v: k for k, v in CREDENTIAL_ENVS.items()}

# Minimal sanity expectations per source (NOT validation — only a red flag for
# obviously mis-pasted values, e.g. an ENTSO-E token pasted into the Ember box).
FORMAT_HINTS: dict[str, dict[str, Any]] = {
    "ember": {"min_len": 16},
    "entsoe": {"min_len": 16},
    "eia": {"min_len": 16},
    "era5": {"min_len": 24},  # CDS key is "<uid>:<long-api-key>"
}


def canonical_env(name: str) -> str:
    """Normalize an environment-variable name to its canonical form."""
    key = (name or "").strip()
    if not key:
        return ""
    return ENV_ALIASES.get(key.upper(), key.upper())


def _mask(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def env_value(env_name: str) -> str | None:
    """Read a value from the environment (with alias tolerance)."""
    canon = canonical_env(env_name)
    val = os.getenv(canon) or os.getenv(env_name) or os.getenv(env_name.upper())
    if val is None and canon in ENV_ALIASES.values():
        # try the other accepted spellings
        for alias, target in ENV_ALIASES.items():
            if target == canon:
                val = os.getenv(alias)
                if val:
                    return val
    return val or None


def load_credentials(explicit: dict[str, str] | None = None) -> dict[str, str]:
    """Merge explicit mapping + environment into one credential dict.

    Keys are canonical env names. Explicit values win over environment values.
    Values are never masked here — callers must not print them.
    """
    creds: dict[str, str] = {}
    # 1. environment
    for env_name in set(CREDENTIAL_ENVS.values()):
        val = env_value(env_name)
        if val:
            creds[env_name] = val
    # 2. explicit (may use either source_id or env-name keys)
    for key, val in (explicit or {}).items():
        if not val:
            continue
        canon = _canonical_key(key)
        if canon:
            creds[canon] = str(val)
    return creds


def _canonical_key(key: str) -> str:
    """Map either a source_id ('ember') or an env name onto a canonical env name."""
    k = (key or "").strip()
    if not k:
        return ""
    if k in CREDENTIAL_ENVS:
        return CREDENTIAL_ENVS[k]
    canon = canonical_env(k)
    if canon in set(CREDENTIAL_ENVS.values()):
        return canon
    # tolerate source display names / bare ids
    if k in ENV_TO_SOURCE:
        return k
    return ""


def get_credential(source_id: str, credentials: dict[str, str] | None = None) -> str | None:
    """Return the credential value for a source_id (canonical env name keyed)."""
    env_name = CREDENTIAL_ENVS.get(source_id)
    if not env_name:
        return None
    if credentials:
        # explicit mapping may be keyed by source_id or env name
        for key in (source_id, env_name):
            if credentials.get(key):
                return credentials[key]
        for k, v in credentials.items():
            if _canonical_key(k) == env_name and v:
                return v
    return env_value(env_name)


def is_supplied(source_id: str, credentials: dict[str, str] | None = None) -> bool:
    return bool(get_credential(source_id, credentials))


def format_ok(source_id: str, credentials: dict[str, str] | None = None) -> tuple[bool, str]:
    """Light sanity check on a supplied credential.

    Returns (ok, note). This only flags obviously wrong values (wrong length /
    whitespace); it can never prove a key is valid against the remote API.
    """
    val = get_credential(source_id, credentials)
    if not val:
        return False, "not supplied"
    hint = FORMAT_HINTS.get(source_id, {})
    min_len = int(hint.get("min_len", 1))
    if len(val.strip()) < min_len:
        return False, f"shorter than expected (len={len(val.strip())}, min={min_len})"
    if re.search(r"\s", val):
        return False, "contains whitespace (possible copy/paste error)"
    return True, "format plausible"


def masked(source_id: str, credentials: dict[str, str] | None = None) -> str:
    """Masked preview for UI status display (NEVER the real value)."""
    return _mask(get_credential(source_id, credentials))


def source_ids() -> list[str]:
    return sorted(CREDENTIAL_ENVS)


__all__ = [
    "CREDENTIAL_ENVS", "ENV_ALIASES", "ENV_TO_SOURCE",
    "load_credentials", "get_credential", "is_supplied",
    "format_ok", "masked", "source_ids", "canonical_env",
]
