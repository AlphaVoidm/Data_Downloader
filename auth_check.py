"""Source authentication / endpoint health checks (the `auth-check` command).

Runs a TINY request per credential-protected source BEFORE any acquisition so
the researcher can tell the difference between:

    * credential missing          (CONFIGURATION_ERROR)
    * credential present but bad  (AUTH_FAILED)
    * credential present + valid  (AUTH_OK)
    * endpoint healthy            (ENDPOINT_OK / ENDPOINT_UNAVAILABLE)

Real credential values are NEVER returned, printed, or logged — only masked
previews and boolean status flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from credential_manager import (
    CREDENTIAL_ENVS,
    format_ok,
    is_supplied,
    masked,
)
from source_registry import get_source
from status_vocabulary import source_status

# --- Result statuses ---------------------------------------------------------
AUTH_OK = "AUTH_OK"
AUTH_FAILED = "AUTH_FAILED"
CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
ENDPOINT_OK = "ENDPOINT_OK"
ENDPOINT_UNAVAILABLE = "ENDPOINT_UNAVAILABLE"
DEPENDENCY_MISSING = "DEPENDENCY_MISSING"
CLIENT_READY = "CLIENT_READY"
NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class AuthCheckResult:
    source_id: str
    source_name: str
    status: str
    message: str
    credential_supplied: bool = False
    credential_format_ok: bool = False
    credential_format_note: str = ""
    masked_credential: str = ""
    http_status: int | None = None
    endpoint_available: bool = False
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


# --- Tiny per-source auth probes --------------------------------------------
def _probe_ember(credentials: dict[str, str] | None) -> dict[str, Any]:
    from connectors.ember import _date_arg, BASE
    from connectors.base import _HTTP, ConnectorError
    key = credentials.get("EMBER_API_KEY") if credentials else None
    history: list[dict[str, Any]] = []
    # Tiny probe: one entity code, one month, monthly demand endpoint.
    url = f"{BASE}electricity-demand/monthly"
    resp = _HTTP.get(url, params={"entity_code": "DEU", "start_date": _date_arg(2024, "monthly"),
                                  "end_date": _date_arg(2024, "monthly", end=True),
                                  "api_key": key}, timeout=30, history=history)
    if resp.status_code in (401, 403):
        return {"status": AUTH_FAILED, "message": f"HTTP {resp.status_code} — key rejected",
                "http_status": resp.status_code, "attempts": history}
    if resp.status_code == 200:
        try:
            payload = resp.json()
            rows = payload.get("data", []) if isinstance(payload, dict) else payload
            ok = isinstance(rows, list)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return {"status": ENDPOINT_OK,
                    "message": f"HTTP 200 — monthly demand endpoint available ({len(rows)} sample row(s))",
                    "http_status": 200, "endpoint_available": True, "attempts": history}
        return {"status": AUTH_FAILED, "message": "HTTP 200 but unexpected response shape",
                "http_status": 200, "attempts": history}
    return {"status": ENDPOINT_UNAVAILABLE, "message": f"HTTP {resp.status_code}",
            "http_status": resp.status_code, "attempts": history}


def _probe_entsoe(credentials: dict[str, str] | None) -> dict[str, Any]:
    from connectors.base import _HTTP, ConnectorError
    from source_mapping import get_primary_area_code
    token = credentials.get("ENTSOE_API_TOKEN") if credentials else None
    eic = get_primary_area_code("DEU", "ENTSO-E Transparency")
    if not eic:
        return {"status": CONFIGURATION_ERROR, "message": "no DEU EIC code in area mapping"}
    history: list[dict[str, Any]] = []
    params = {
        "securityToken": token, "documentType": "A65", "processType": "A16",
        "outBiddingZone_Domain": eic, "periodStart": "202401010000", "periodEnd": "202401020000",
    }
    resp = _HTTP.get("https://web-api.tp.entsoe.eu/api", params=params, timeout=60, history=history)
    if resp.status_code in (401, 403):
        return {"status": AUTH_FAILED, "message": f"HTTP {resp.status_code} — token rejected",
                "http_status": resp.status_code, "attempts": history}
    if resp.status_code == 200 and b"<" in resp.content[:200]:
        return {"status": ENDPOINT_OK, "message": "HTTP 200 — XML returned",
                "http_status": 200, "endpoint_available": True, "attempts": history}
    return {"status": ENDPOINT_UNAVAILABLE, "message": f"HTTP {resp.status_code}",
            "http_status": resp.status_code, "attempts": history}


def _probe_eia(credentials: dict[str, str] | None) -> dict[str, Any]:
    from connectors.base import _HTTP
    key = credentials.get("EIA_API_KEY") if credentials else None
    history: list[dict[str, Any]] = []
    params = {"api_key": key, "data[0]": "value", "facets[type][]": "D",
              "length": 1, "start": "2024-01-01T00", "end": "2024-01-01T01"}
    resp = _HTTP.get("https://api.eia.gov/v2/electricity/rto/region-data/data/",
                     params=params, timeout=30, history=history)
    if resp.status_code in (401, 403):
        return {"status": AUTH_FAILED, "message": f"HTTP {resp.status_code} — key rejected",
                "http_status": resp.status_code, "attempts": history}
    if resp.status_code == 200:
        return {"status": ENDPOINT_OK, "message": "HTTP 200 — API v2 reachable",
                "http_status": 200, "endpoint_available": True, "attempts": history}
    return {"status": ENDPOINT_UNAVAILABLE, "message": f"HTTP {resp.status_code}",
            "http_status": resp.status_code, "attempts": history}


def _probe_cds(credentials: dict[str, str] | None) -> dict[str, Any]:
    """CDS is a queued bulk job — a live retrieve is inappropriate for an auth
    probe. Instead verify the credential format and that cdsapi can construct
    a client (no network)."""
    key = credentials.get("CDS_API_KEY") if credentials else None
    if not key:
        return {"status": CONFIGURATION_ERROR, "message": "CDS_API_KEY not configured"}
    if ":" not in key:
        return {"status": CONFIGURATION_ERROR, "message": "CDS key should be <uid>:<api-key>"}
    try:
        import cdsapi  # noqa: F401
    except ImportError:
        return {"status": DEPENDENCY_MISSING, "message": "cdsapi not installed"}
    try:
        import cdsapi as c
        url = (credentials or {}).get("CDS_API_URL") or None
        client = c.Client(url=url or "https://cds.climate.copernicus.eu/api", key=key) if key else c.Client()
        _ = client  # construction only — no network call
        return {"status": CLIENT_READY, "message": "cdsapi client constructed (queue-based; no live job submitted)",
                "endpoint_available": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": DEPENDENCY_MISSING, "message": f"cdsapi client failed: {str(exc)[:160]}"}


_PROBES = {
    "ember": _probe_ember,
    "entsoe": _probe_entsoe,
    "eia": _probe_eia,
    "era5": _probe_cds,
}


def run_auth_checks(credentials: dict[str, str] | None = None) -> list[AuthCheckResult]:
    """Run the tiny auth/endpoint probe for every credential-protected source.

    Does not require the credentials themselves to be present — a missing key
    yields a CONFIGURATION_ERROR result so the researcher sees WHAT is missing.
    """
    results: list[AuthCheckResult] = []
    for source_id in sorted(CREDENTIAL_ENVS):
        src = get_source(source_id)
        source_name = src.source_name if src else source_id
        supplied = is_supplied(source_id, credentials)
        fmt_ok, fmt_note = format_ok(source_id, credentials)
        mask = masked(source_id, credentials)

        probe = _PROBES.get(source_id)
        if probe is None or not supplied:
            results.append(AuthCheckResult(
                source_id=source_id, source_name=source_name,
                status=CONFIGURATION_ERROR,
                message="credential not supplied" if not supplied else "no probe registered",
                credential_supplied=supplied, credential_format_ok=fmt_ok,
                credential_format_note=fmt_note, masked_credential=mask,
            ))
            continue

        try:
            probe_result = probe(credentials)
        except Exception as exc:  # noqa: BLE001
            probe_result = {"status": ENDPOINT_UNAVAILABLE,
                            "message": f"probe failed: {str(exc)[:160]}"}

        probe_status = probe_result.get("status", ENDPOINT_UNAVAILABLE)
        results.append(AuthCheckResult(
            source_id=source_id, source_name=source_name,
            status=probe_status,
            message=probe_result.get("message", ""),
            credential_supplied=supplied, credential_format_ok=fmt_ok,
            credential_format_note=fmt_note, masked_credential=mask,
            http_status=probe_result.get("http_status"),
            endpoint_available=bool(probe_result.get("endpoint_available")),
            attempts=probe_result.get("attempts", []),
        ))
    return results


def render_auth_check(results: list[AuthCheckResult]) -> str:
    lines = ["", "SOURCE AUTHENTICATION CHECK", "=" * 88]
    for r in results:
        lines.append("")
        lines.append(f"{r.source_name}  ({r.source_id})")
        lines.append("-" * 60)
        lines.append(f"  Credential supplied : {'YES' if r.credential_supplied else 'NO'}")
        lines.append(f"  Credential format   : {'VALID' if r.credential_format_ok else 'INVALID'}"
                     + (f" ({r.credential_format_note})" if r.credential_format_note else ""))
        lines.append(f"  Credential preview  : {r.masked_credential or '(none)'}")
        if r.http_status is not None:
            lines.append(f"  API response        : HTTP {r.http_status}")
        lines.append(f"  Result              : {r.status}")
        if r.endpoint_available:
            lines.append("  Endpoint            : AVAILABLE")
        if r.attempts:
            lines.append(f"  Attempts            : {len(r.attempts)}")
        lines.append(f"  Detail              : {r.message}")
    return "\n".join(lines)


__all__ = [
    "AuthCheckResult", "run_auth_checks", "render_auth_check",
    "AUTH_OK", "AUTH_FAILED", "CONFIGURATION_ERROR", "ENDPOINT_OK",
    "ENDPOINT_UNAVAILABLE", "DEPENDENCY_MISSING", "CLIENT_READY", "NOT_APPLICABLE",
]
