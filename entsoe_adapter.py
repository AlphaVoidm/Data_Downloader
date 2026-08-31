"""ENTSO-E Transparency Platform Adapter for European Hourly Grid Load.

Downloads official Actual Total Load [6.1.A] using verified EIC Area Codes.

KEY CORRECTIONS vs earlier version:
  - Base URL: https://web-api.tp.entsoe.eu/api  (NOT transparency.entsoe.eu/api)
  - Load query parameter: outBiddingZone_Domain  (NOT outBzn_Domain)
  - GBR (Great Britain): left the ENTSO-E synchronous area in Jan 2021 post-Brexit;
    data is limited/absent after that date — use ESO/NESO for current GB demand.

Implements robust pre-parse response validation:
1. Checks HTTP status code.
2. Records and verifies Content-Type.
3. Captures and previews the first 500-1000 chars on error.
4. Distinguishes non-XML payloads from XML parser syntax errors.
5. Surfaces exact parser exceptions with request metadata (secrets redacted).
6. Distinguishes OUT_OF_SCOPE, MAPPING_MISSING, ACCESS_RESTRICTED, API_ERROR,
   PARSE_ERROR, NO_DATA_AVAILABLE, and SUCCESS.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from provenance import generate_file_sidecar
from source_mapping import get_primary_area_code


def get_entsoe_total_load(
    country_iso3: str,
    start_year: int,
    end_year: int,
    api_token: str | None = None,
) -> dict[str, Any]:
    """
    Fetch Actual Total Load from ENTSO-E Transparency API using verified EIC code.

    DocumentType: A65 (System total load)
    ProcessType: A16 (Realised)
    """
    token = api_token or os.getenv("ENTSOE_API_TOKEN") or os.getenv("ENTSOE_API_KEY")
    if not token:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": "ENTSO-E Transparency API requires ENTSOE_API_TOKEN",
            "status_type": "ACCESS_RESTRICTED",
        }

    eic_code = get_primary_area_code(country_iso3, "ENTSO-E Transparency")
    if not eic_code:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": f"No verified EIC area code registered for {country_iso3}",
            "status_type": "MAPPING_MISSING",
        }

    period_start = f"{start_year}01010000"
    period_end = f"{end_year}12312300"

    # Correct base URL (web-api subdomain, not transparency subdomain)
    # Correct parameter name: outBiddingZone_Domain (not outBzn_Domain)
    url = "https://web-api.tp.entsoe.eu/api"
    params = {
        "securityToken": token,
        "documentType": "A65",
        "processType": "A16",
        "outBiddingZone_Domain": eic_code,
        "periodStart": period_start,
        "periodEnd": period_end,
    }

    # Post-Brexit warning for GBR: data reliability limited after Jan 2021
    gbr_warning = None
    if country_iso3 == "GBR" and end_year >= 2021:
        gbr_warning = (
            "GBR left the ENTSO-E synchronous area in Jan 2021 (post-Brexit). "
            "ENTSO-E data for GBR is unreliable/absent after 2021. "
            "Use ESO/NESO as the primary source for current GB electricity demand."
        )
    sanitized_params = {k: ("[REDACTED]" if "token" in k.lower() else v) for k, v in params.items()}

    try:
        resp = requests.get(url, params=params, timeout=60)
        http_status = resp.status_code
        content_type = resp.headers.get("Content-Type", "")
        response_text = resp.text or ""
        preview = response_text[:800].strip()

        # Step 1 & 4: Check HTTP status code
        if http_status != 200:
            if http_status in [401, 403]:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": f"HTTP {http_status} Access Denied. Content-Type: {content_type}. Preview: {preview[:200]}",
                    "status_type": "ACCESS_RESTRICTED",
                    "http_status": http_status,
                    "content_type": content_type,
                    "response_preview": preview,
                }
            elif http_status == 429:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": f"HTTP 429 Rate Limited from ENTSO-E. Preview: {preview[:200]}",
                    "status_type": "API_ERROR",
                    "http_status": http_status,
                    "content_type": content_type,
                    "response_preview": preview,
                }
            else:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": (
                        f"HTTP {http_status} API Error from ENTSO-E. Content-Type: '{content_type}'. "
                        f"Params: {sanitized_params}. Preview: {preview[:300]}"
                    ),
                    "status_type": "API_ERROR",
                    "http_status": http_status,
                    "content_type": content_type,
                    "response_preview": preview,
                }

        # Step 2 & 5: Check Content-Type & XML format
        is_html = "html" in content_type.lower() or response_text.strip().lower().startswith("<!doctype html") or response_text.strip().lower().startswith("<html")
        is_json = "json" in content_type.lower() or response_text.strip().startswith("{") or response_text.strip().startswith("[")
        is_xml = ("xml" in content_type.lower() or response_text.strip().startswith("<?xml") or ("<" in response_text and "MarketDocument" in response_text)) and not (is_html or is_json)

        if not is_xml:
            return {
                "success": False,
                "data": None,
                "records": 0,
                "message": (
                    f"Non-XML response format received from ENTSO-E. Content-Type: '{content_type}'. "
                    f"HTTP Status: {http_status}. Preview: {preview[:300]}"
                ),
                "status_type": "PARSE_ERROR",
                "http_status": http_status,
                "content_type": content_type,
                "response_preview": preview,
            }

        # Step 6 & 7: Attempt XML parsing
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as parse_exc:
            return {
                "success": False,
                "data": None,
                "records": 0,
                "message": (
                    f"XML Parser Error: {parse_exc} | HTTP Status: {http_status} | "
                    f"Content-Type: '{content_type}' | Params: {sanitized_params} | "
                    f"Preview: {preview[:400]}"
                ),
                "status_type": "PARSE_ERROR",
                "http_status": http_status,
                "content_type": content_type,
                "response_preview": preview,
                "parser_error": str(parse_exc),
            }

        # Step 8 & 9: Inspect parsed XML for acknowledgement / error / empty data
        ack_reason = root.find(".//{*}Reason/{*}text")
        ack_code = root.find(".//{*}Reason/{*}code")
        if ack_reason is not None:
            reason_text = ack_reason.text or ""
            code_text = ack_code.text if ack_code is not None else ""
            if "No matching data found" in reason_text or "999" in code_text:
                return {
                    "success": True,
                    "data": pd.DataFrame(),
                    "records": 0,
                    "message": f"ENTSO-E verified empty data: '{reason_text}' for {eic_code} ({start_year}-{end_year})",
                    "status_type": "NO_DATA_AVAILABLE",
                    "http_status": http_status,
                    "content_type": content_type,
                }
            else:
                return {
                    "success": False,
                    "data": None,
                    "records": 0,
                    "message": f"ENTSO-E Acknowledgement error: '{reason_text}' (Code: {code_text})",
                    "status_type": "API_ERROR",
                    "http_status": http_status,
                    "content_type": content_type,
                    "response_preview": preview[:300],
                }

        # Parse TimeSeries records
        records = []
        for ts in root.findall(".//{*}TimeSeries"):
            period = ts.find("{*}Period")
            if period is None:
                continue
            start_str = period.findtext("{*}timeInterval/{*}start")
            resolution = period.findtext("{*}resolution")

            for point in period.findall("{*}Point"):
                pos = point.findtext("{*}position")
                qty = point.findtext("{*}quantity")
                if qty is not None:
                    records.append({
                        "iso3": country_iso3,
                        "eic_area_code": eic_code,
                        "period_start_utc": start_str,
                        "position": int(pos) if pos else None,
                        "resolution": resolution,
                        "actual_total_load_mw": float(qty),
                        "unit": "MW",
                        "concept": "electricity_demand",
                        "source_variable": "Actual Total Load [6.1.A]",
                        "source": "ENTSO-E Transparency",
                        "frequency": "hourly" if resolution == "PT60M" else "sub-hourly",
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    })

        if not records:
            return {
                "success": True,
                "data": pd.DataFrame(),
                "records": 0,
                "message": f"No TimeSeries observations found in valid ENTSO-E document for {eic_code}",
                "status_type": "NO_DATA_AVAILABLE",
                "http_status": http_status,
                "content_type": content_type,
            }

        df = pd.DataFrame(records)
        msg = f"{len(df):,} hourly grid load observations retrieved for {eic_code}"
        if gbr_warning:
            msg += f" | WARNING: {gbr_warning}"
        return {
            "success": True,
            "data": df,
            "records": len(df),
            "message": msg,
            "status_type": "SUCCESS",
            "http_status": http_status,
            "content_type": content_type,
            "gbr_post_brexit_warning": gbr_warning,
        }

    except requests.RequestException as exc:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": f"Network / HTTP connection error to ENTSO-E: {str(exc)[:200]}",
            "status_type": "DOWNLOAD_ERROR",
        }
    except Exception as exc:
        return {
            "success": False,
            "data": None,
            "records": 0,
            "message": f"Unhandled ENTSO-E processing error: {str(exc)[:200]}",
            "status_type": "PARSE_ERROR",
        }


def save_entsoe_data(data: pd.DataFrame, output_path: Path, country_iso3: str) -> None:
    """Save raw ENTSO-E load data preserving exact native schema."""
    if data is None or data.empty:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data.to_csv(output_path, index=False)


def smoke_test_entsoe(api_token: str, eic_code: str = "10YFR-RTE------C", country_iso3: str = "FRA") -> dict:
    """
    Minimal smoke test against the ENTSO-E API.

    Sends a 2-day request to a known active bidding zone (France by default)
    and verifies the full pipeline: HTTP status → Content-Type → XML parse → records.

    Returns a dict with keys: success, http_status, content_type, records, message.
    """
    url = "https://web-api.tp.entsoe.eu/api"
    params = {
        "securityToken": api_token,
        "documentType": "A65",
        "processType": "A16",
        "outBiddingZone_Domain": eic_code,
        "periodStart": "202401010000",
        "periodEnd": "202401020000",
    }

    result = {
        "success": False,
        "http_status": None,
        "content_type": None,
        "records": 0,
        "message": "",
        "eic_tested": eic_code,
        "country_tested": country_iso3,
        "url_used": url,
        "param_used": "outBiddingZone_Domain",
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        result["http_status"] = resp.status_code
        result["content_type"] = resp.headers.get("Content-Type", "")

        if resp.status_code != 200:
            result["message"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
            return result

        if "html" in result["content_type"].lower():
            result["message"] = (
                f"API returned HTML instead of XML (Content-Type: {result['content_type']}). "
                "This usually means wrong URL or malformed request. "
                f"Preview: {resp.text[:300]}"
            )
            return result

        root_xml = ET.fromstring(resp.content)
        records = []
        for ts in root_xml.findall(".//{*}TimeSeries"):
            for period in ts.findall("{*}Period"):
                for point in period.findall("{*}Point"):
                    qty = point.findtext("{*}quantity")
                    if qty:
                        records.append(qty)

        result["records"] = len(records)
        if records:
            result["success"] = True
            result["message"] = f"Smoke test PASSED: {len(records)} load points retrieved for {eic_code}"
        else:
            result["message"] = f"Smoke test: XML parsed OK but 0 records found for {eic_code}"

    except ET.ParseError as e:
        result["message"] = f"XML parse error: {e}"
    except Exception as e:
        result["message"] = f"Smoke test error: {e}"

    return result
