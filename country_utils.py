"""Country utilities for normalization, edge-case resolution, coordinates, and regional presets.

Handles ISO-3, ISO-2, common aliases, disputed territories, historical entities,
and spatial centroids for weather engines like NASA POWER.
"""
from __future__ import annotations

import re
from typing import Any
import pycountry

# Canonical aliases and edge cases mapping to ISO-3
COUNTRY_ALIASES: dict[str, str] = {
    # Disputed territories & custom codes
    "KOSOVO": "XKX",
    "XKX": "XKX",
    "TAIWAN": "TWN",
    "TAIWAN, PROVINCE OF CHINA": "TWN",
    "CHINESE TAIPEI": "TWN",
    "TWN": "TWN",
    "PALESTINE": "PSE",
    "PALESTINE, STATE OF": "PSE",
    "WEST BANK AND GAZA": "PSE",
    "GAZA STRIP": "PSE",
    "PSE": "PSE",
    "HONG KONG": "HKG",
    "HONG KONG SAR, CHINA": "HKG",
    "HKG": "HKG",
    "MACAO": "MAC",
    "MACAU": "MAC",
    "MACAO SAR, CHINA": "MAC",
    "MAC": "MAC",
    "VATICAN": "VAT",
    "VATICAN CITY": "VAT",
    "HOLY SEE": "VAT",
    "VAT": "VAT",

    # Common names and variations
    "USA": "USA",
    "UNITED STATES": "USA",
    "UNITED STATES OF AMERICA": "USA",
    "US": "USA",
    "UK": "GBR",
    "UNITED KINGDOM": "GBR",
    "GREAT BRITAIN": "GBR",
    "BRITAIN": "GBR",
    "ENGLAND": "GBR",
    "SCOTLAND": "GBR",
    "WALES": "GBR",
    "NORTHERN IRELAND": "GBR",
    "GB": "GBR",
    "GBR": "GBR",
    "UAE": "ARE",
    "UNITED ARAB EMIRATES": "ARE",
    "AE": "ARE",
    "RUSSIA": "RUS",
    "RUSSIAN FEDERATION": "RUS",
    "SOUTH KOREA": "KOR",
    "KOREA, SOUTH": "KOR",
    "KOREA, REPUBLIC OF": "KOR",
    "KOREA, REP.": "KOR",
    "REPUBLIC OF KOREA": "KOR",
    "NORTH KOREA": "PRK",
    "KOREA, NORTH": "PRK",
    "KOREA, DEM. PEOPLE'S REP.": "PRK",
    "DEMOCRATIC PEOPLE'S REPUBLIC OF KOREA": "PRK",
    "VIETNAM": "VNM",
    "VIET NAM": "VNM",
    "SYRIA": "SYR",
    "SYRIAN ARAB REPUBLIC": "SYR",
    "IRAN": "IRN",
    "IRAN, ISLAMIC REPUBLIC OF": "IRN",
    "IRAN, ISLAMIC REP.": "IRN",
    "EGYPT": "EGY",
    "EGYPT, ARAB REP.": "EGY",
    "CONGO, DEM. REP.": "COD",
    "DEMOCRATIC REPUBLIC OF THE CONGO": "COD",
    "DR CONGO": "COD",
    "CONGO, DR": "COD",
    "CONGO, DEMOCRATIC REPUBLIC OF THE": "COD",
    "CONGO, REP.": "COG",
    "CONGO, REPUBLIC OF THE": "COG",
    "REPUBLIC OF THE CONGO": "COG",
    "CONGO-BRAZZAVILLE": "COG",
    "TANZANIA": "TZA",
    "TANZANIA, UNITED REPUBLIC OF": "TZA",
    "BOLIVIA": "BOL",
    "BOLIVIA, PLURINATIONAL STATE OF": "BOL",
    "VENEZUELA": "VEN",
    "VENEZUELA, BOLIVARIAN REPUBLIC OF": "VEN",
    "VENEZUELA, RB": "VEN",
    "LAOS": "LAO",
    "LAO PEOPLE'S DEMOCRATIC REPUBLIC": "LAO",
    "LAO PDR": "LAO",
    "MOLDOVA": "MDA",
    "MOLDOVA, REPUBLIC OF": "MDA",
    "COTE D'IVOIRE": "CIV",
    "CÔTE D'IVOIRE": "CIV",
    "IVORY COAST": "CIV",
    "CABO VERDE": "CPV",
    "CAPE VERDE": "CPV",
    "CZECH REPUBLIC": "CZE",
    "CZECHIA": "CZE",
    "ESWATINI": "SWZ",
    "SWAZILAND": "SWZ",
    "NORTH MACEDONIA": "MKD",
    "MACEDONIA": "MKD",
    "MACEDONIA, FYR": "MKD",
    "EAST TIMOR": "TLS",
    "TIMOR-LESTE": "TLS",
    "BURMA": "MMR",
    "MYANMAR": "MMR",
    "MICRONESIA": "FSM",
    "MICRONESIA, FEDERATED STATES OF": "FSM",
    "TURKEY": "TUR",
    "TÜRKIYE": "TUR",
    "TURKIYE": "TUR",
    "BRUNEI": "BRN",
    "BRUNEI DARUSSALAM": "BRN",
    "BAHAMAS": "BHS",
    "BAHAMAS, THE": "BHS",
    "GAMBIA": "GMB",
    "GAMBIA, THE": "GMB",
    "ST. LUCIA": "LCA",
    "SAINT LUCIA": "LCA",
    "ST. VINCENT AND THE GRENADINES": "VCT",
    "SAINT VINCENT AND THE GRENADINES": "VCT",
    "ST. KITTS AND NEVIS": "KNA",
    "SAINT KITTS AND NEVIS": "KNA",
}

# Historical country mapping for warnings / legacy support
HISTORICAL_COUNTRIES: dict[str, str] = {
    "USSR": "RUS",
    "SOVIET UNION": "RUS",
    "YUGOSLAVIA": "SRB",
    "CZECHOSLOVAKIA": "CZE",
    "EAST GERMANY": "DEU",
    "WEST GERMANY": "DEU",
    "ZAIRE": "COD",
    "BURMA (HISTORICAL)": "MMR",
}

# 200+ Country Centroid Coordinates (Latitude, Longitude) for spatial querying (e.g. NASA POWER)
COUNTRY_CENTROIDS: dict[str, tuple[float, float]] = {
    "AFG": (33.9391, 67.7100), "ALB": (41.1533, 20.1683), "DZA": (28.0339, 1.6596),
    "AND": (42.5063, 1.5218), "AGO": (-11.2027, 17.8739), "ATG": (17.0608, -61.7964),
    "ARG": (-38.4161, -63.6167), "ARM": (40.0691, 45.0382), "AUS": (-25.2744, 133.7751),
    "AUT": (47.5162, 14.5501), "AZE": (40.1431, 47.5769), "BHS": (25.0343, -77.3963),
    "BHR": (26.0667, 50.5577), "BGD": (23.6850, 90.3563), "BRB": (13.1939, -59.5432),
    "BLR": (53.7098, 27.9534), "BEL": (50.5039, 4.4699), "BLZ": (17.1899, -88.4976),
    "BEN": (9.3077, 2.3158), "BTN": (27.5142, 90.4336), "BOL": (-16.2902, -63.5887),
    "BIH": (43.9159, 17.6791), "BWA": (-22.3285, 24.6849), "BRA": (-14.2350, -51.9253),
    "BRN": (4.5353, 114.7277), "BGR": (42.7339, 25.4858), "BFA": (12.2383, -1.5616),
    "BDI": (-3.3731, 29.9189), "CPV": (16.5388, -23.0418), "KHM": (12.5657, 104.9910),
    "CMR": (7.3697, 12.3547), "CAN": (56.1304, -106.3468), "CAF": (6.6111, 20.9394),
    "TCD": (15.4542, 18.7322), "CHL": (-35.6751, -71.5430), "CHN": (35.8617, 104.1954),
    "COL": (4.5709, -74.2973), "COM": (-11.8753, 43.8722), "COG": (-0.2280, 15.8277),
    "COD": (-4.0383, 21.7587), "CRI": (9.7489, -83.7534), "CIV": (7.5400, -5.5471),
    "HRV": (45.1000, 15.2000), "CUB": (21.5218, -77.7812), "CYP": (35.1264, 33.4299),
    "CZE": (49.8175, 15.4730), "DNK": (56.2639, 9.5018), "DJI": (11.8251, 42.5903),
    "DMA": (15.4150, -61.3710), "DOM": (18.7357, -70.1627), "ECU": (-1.8312, -78.1834),
    "EGY": (26.8206, 30.8025), "SLV": (13.7942, -88.8965), "GNQ": (1.6508, 10.2679),
    "ERI": (15.1794, 39.7823), "EST": (58.5953, 25.0136), "SWZ": (-26.5225, 31.4659),
    "ETH": (9.1450, 40.4897), "FJI": (-17.7134, 178.0650), "FIN": (61.9241, 25.7482),
    "FRA": (46.2276, 2.2137), "GAB": (-0.8037, 11.6094), "GMB": (13.4432, -15.3101),
    "GEO": (42.3154, 43.3569), "DEU": (51.1657, 10.4515), "GHA": (7.9465, -1.0232),
    "GRC": (39.0742, 21.8243), "GRD": (12.1165, -61.6790), "GTM": (15.7835, -90.2308),
    "GIN": (9.9456, -9.6966), "GNB": (11.8037, -15.1804), "GUY": (4.8604, -58.9302),
    "HTI": (18.9712, -72.2852), "HND": (15.2000, -86.2419), "HKG": (22.3193, 114.1694),
    "HUN": (47.1625, 19.5033), "ISL": (64.9631, -19.0208), "IND": (20.5937, 78.9629),
    "IDN": (-0.7893, 113.9213), "IRN": (32.4279, 53.6880), "IRQ": (33.2232, 43.6793),
    "IRL": (53.1424, -7.6921), "ISR": (31.0461, 34.8516), "ITA": (41.8719, 12.5674),
    "JAM": (18.1096, -77.2975), "JPN": (36.2048, 138.2529), "JOR": (30.5852, 36.2384),
    "KAZ": (48.0196, 66.9237), "KEN": (-0.0236, 37.9062), "KIR": (-3.3704, -168.7340),
    "PRK": (40.3399, 127.5101), "KOR": (35.9078, 127.7669), "XKX": (42.6026, 20.9030),
    "KWT": (29.3117, 47.4818), "KGZ": (41.2044, 74.7661), "LAO": (19.8563, 102.4955),
    "LVA": (56.8796, 24.6032), "LBN": (33.8547, 35.8623), "LSO": (-29.6099, 28.2336),
    "LBR": (6.4281, -9.4295), "LBY": (26.3351, 17.2283), "LIE": (47.1660, 9.5554),
    "LTU": (55.1694, 23.8813), "LUX": (49.8153, 6.1296), "MAC": (22.1987, 113.5439),
    "MDG": (-18.7669, 46.8691), "MWI": (-13.2543, 34.3015), "MYS": (4.2105, 101.9758),
    "MDV": (3.2028, 73.2207), "MLI": (17.5707, -3.9962), "MLT": (35.9375, 14.3754),
    "MRT": (21.0079, -10.9408), "MUS": (-20.3484, 57.5522), "MEX": (23.6345, -102.5528),
    "MDA": (47.4116, 28.3699), "MCO": (43.7384, 7.4246), "MNG": (46.8625, 103.8467),
    "MNE": (42.7087, 19.3744), "MAR": (31.7917, -7.0926), "MOZ": (-18.6657, 35.5296),
    "MMR": (21.9162, 95.9560), "NAM": (-22.9576, 18.4904), "NRU": (-0.5228, 166.9315),
    "NPL": (28.3949, 84.1240), "NLD": (52.1326, 5.2913), "NZL": (-40.9006, 174.8860),
    "NIC": (12.8654, -85.2072), "NER": (17.6078, 8.0817), "NGA": (9.0820, 8.6753),
    "MKD": (41.6086, 21.7453), "NOR": (60.4720, 8.4689), "OMN": (21.5126, 55.9233),
    "PAK": (30.3753, 69.3451), "PLW": (7.5150, 134.5825), "PSE": (31.9522, 35.2332),
    "PAN": (8.5380, -80.7821), "PNG": (-6.3150, 143.9555), "PRY": (-23.4425, -58.4438),
    "PER": (-9.1900, -75.0152), "PHL": (12.8797, 121.7740), "POL": (51.9194, 19.1451),
    "PRT": (39.3999, -8.2245), "QAT": (25.3548, 51.1839), "ROU": (45.9432, 24.9668),
    "RUS": (61.5240, 105.3188), "RWA": (-1.9403, 29.8739), "WSM": (-13.7590, -172.1046),
    "SMR": (43.9424, 12.4578), "STP": (0.1864, 6.6131), "SAU": (23.8859, 45.0792),
    "SEN": (14.4974, -14.4524), "SRB": (44.0165, 21.0059), "SYC": (-4.6796, 55.4920),
    "SLE": (8.4606, -11.7799), "SGP": (1.3521, 103.8198), "SVK": (48.6690, 19.6990),
    "SVN": (46.1512, 14.9955), "SLB": (-9.6457, 160.1562), "SOM": (5.1521, 46.1996),
    "ZAF": (-30.5595, 22.9375), "SSD": (6.8770, 31.3070), "ESP": (40.4637, -3.7492),
    "LKA": (7.8731, 80.7718), "SDN": (12.8628, 30.2176), "SUR": (3.9193, -56.0278),
    "SWE": (60.1282, 18.6435), "CHE": (46.8182, 8.2275), "SYR": (34.8021, 38.9968),
    "TWN": (23.6978, 120.9605), "TJK": (38.8610, 71.2761), "TZA": (-6.3690, 34.8888),
    "THA": (15.8700, 100.9925), "TLS": (-8.8742, 125.7275), "TGO": (8.6195, 0.8248),
    "TON": (-21.1790, -175.1982), "TTO": (10.6918, -61.2225), "TUN": (33.8869, 9.5375),
    "TUR": (38.9637, 35.2433), "TKM": (38.9697, 59.5563), "TUV": (-7.1095, 177.6493),
    "UGA": (1.3733, 32.2903), "UKR": (48.3794, 31.1656), "ARE": (23.4241, 53.8478),
    "GBR": (55.3781, -3.4360), "USA": (37.0902, -95.7129), "URY": (-32.5228, -55.7658),
    "UZB": (41.3775, 64.5853), "VUT": (-15.3767, 166.9592), "VAT": (41.9029, 12.4534),
    "VEN": (6.4238, -66.5897), "VNM": (14.0583, 108.2772), "YEM": (15.5527, 48.5164),
    "ZMB": (-13.1339, 27.8493), "ZWE": (-19.0154, 29.1549),
}

# Regional country presets for quick selection in UI
REGIONAL_PRESETS: dict[str, list[str]] = {
    "G7": ["USA", "GBR", "DEU", "FRA", "JPN", "ITA", "CAN"],
    "G20 (Sample)": ["USA", "CHN", "JPN", "DEU", "IND", "GBR", "FRA", "ITA", "BRA", "CAN", "RUS", "KOR", "AUS", "MEX", "IDN", "SAU", "TUR", "ARG", "ZAF"],
    "EU-27 (Sample)": ["DEU", "FRA", "ITA", "ESP", "POL", "NLD", "BEL", "SWE", "AUT", "DNK", "FIN", "PRT", "GRC", "CZE", "ROU", "HUN", "IRL"],
    "Africa (Top 12)": ["EGY", "NGA", "ZAF", "DZA", "MAR", "AGO", "ETH", "KEN", "GHA", "CIV", "CMR", "TZA"],
    "Middle East (Sample)": ["EGY", "SAU", "ARE", "IRN", "IRQ", "JOR", "KWT", "OMN", "QAT", "LBN"],
    "Asia-Pacific (Sample)": ["CHN", "JPN", "IND", "KOR", "AUS", "IDN", "MYS", "THA", "VNM", "PHL", "NZL", "SGP"],
    "Latin America (Sample)": ["BRA", "MEX", "ARG", "COL", "CHL", "PER", "ECU", "VEN", "BOL", "URY"],
}


def normalize_country(value: str) -> str | None:
    """
    Normalize country name, ISO-2, ISO-3, or common alias into standardized ISO-3 code.

    Returns:
        Standard 3-letter ISO-3 code, or None if unrecognized.
    """
    if not value or not isinstance(value, str) or not value.strip():
        return None

    cleaned = value.strip().upper()
    cleaned_clean = re.sub(r"[^\w\s]", "", cleaned).strip()

    # 1. Direct match in aliases dictionary
    if cleaned in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[cleaned]
    if cleaned_clean in COUNTRY_ALIASES:
        return COUNTRY_ALIASES[cleaned_clean]

    # 2. Historical entities mapping
    if cleaned in HISTORICAL_COUNTRIES:
        return HISTORICAL_COUNTRIES[cleaned]

    # 3. Direct 3-letter alpha_3 code lookup
    if len(cleaned) == 3 and cleaned.isalpha():
        c = pycountry.countries.get(alpha_3=cleaned)
        if c:
            return c.alpha_3
        # Handle Kosovo special code
        if cleaned == "XKX":
            return "XKX"

    # 4. Direct 2-letter alpha_2 code lookup
    if len(cleaned) == 2 and cleaned.isalpha():
        c = pycountry.countries.get(alpha_2=cleaned)
        if c:
            return c.alpha_3

    # 5. Direct exact name lookup
    c = pycountry.countries.get(name=value.strip())
    if c:
        return c.alpha_3

    # 6. Fuzzy matching with an explicit similarity threshold so that
    #    non-country words ("Country", "Continent", headers) are not
    #    spuriously matched to a country.
    try:
        import difflib
        target = cleaned_clean
        candidates: dict[str, str] = {}
        for c in pycountry.countries:
            for name in (c.name, getattr(c, "official_name", None), getattr(c, "common_name", None)):
                if name:
                    candidates[name.casefold()] = c.alpha_3
        for alias in COUNTRY_ALIASES:
            candidates[alias.casefold()] = COUNTRY_ALIASES[alias]

        close = difflib.get_close_matches(target, candidates.keys(), n=1, cutoff=0.72)
        if close:
            return candidates[close[0]]
    except Exception:
        pass

    return None


def get_country_name(iso3: str) -> str:
    """Return standard human-readable display name for an ISO-3 code."""
    if not iso3:
        return ""
    iso3_clean = iso3.strip().upper()
    if iso3_clean == "XKX":
        return "Kosovo"
    if iso3_clean == "TWN":
        return "Taiwan"
    if iso3_clean == "PSE":
        return "Palestine"
    try:
        record = pycountry.countries.get(alpha_3=iso3_clean)
        return record.name if record else iso3_clean
    except Exception:
        return iso3_clean


def get_country_coordinates(iso3: str) -> tuple[float, float] | None:
    """Return centroid (latitude, longitude) for spatial queries, or None if unavailable."""
    if not iso3:
        return None
    return COUNTRY_CENTROIDS.get(iso3.strip().upper())


def get_preset_countries(preset_name: str) -> list[str]:
    """Retrieve list of ISO-3 country codes for a given regional preset."""
    return REGIONAL_PRESETS.get(preset_name, [])
