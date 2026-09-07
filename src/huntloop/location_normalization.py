"""Canonicalize the messy free-text ``job_locations.location_name`` values
onto a stable (city, region, country) grouping so filtering groups the
same real place together.

This is deliberately NOT radius / lat-long / geocoding search - it is
pure text canonicalization backed by an OFFLINE gazetteer
(``geonamescache``: ~34k cities of population > 15k, the 50 US states +
DC, and ~250 countries). No network calls, no API keys, no rate limits,
fully deterministic - the same reasoning that made a rules-first design
right for ``department_category`` applies even more strongly here,
because place names resolve cleanly against a gazetteer where department
strings did not resolve cleanly against keywords.

``normalize_location()`` returns a :class:`NormalizedLocation`:

- ``city`` / ``region`` / ``country`` - the resolved hierarchy. ``region``
  is a 2-letter US state / Canadian province code only (other countries'
  sub-national codes collide with US state abbreviations - see
  ``Chennai, TN`` - so they are left unset rather than mis-parsed).
- ``is_remote`` - the string mentions remote / distributed / anywhere.
- ``canonical`` - a display label ("San Francisco, CA, United States",
  "Remote - United States", "London, United Kingdom"); falls back to the
  cleaned raw string when nothing resolves.
- ``resolved`` - whether anything at all was pinned down.

``split_location_string()`` breaks a single joined multi-location value
("San Francisco, CA; New York, NY", "A / B / C") into its parts so the
pipeline can store one row per real place.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

import geonamescache

_CANADA_PROVINCES = {
    "ab": "AB", "bc": "BC", "mb": "MB", "nb": "NB", "nl": "NL", "ns": "NS",
    "nt": "NT", "nu": "NU", "on": "ON", "pe": "PE", "qc": "QC", "sk": "SK",
    "yt": "YT",
}
_CANADA_PROVINCE_NAMES = {
    "alberta": "AB", "british columbia": "BC", "ontario": "ON", "quebec": "QC",
    "manitoba": "MB", "saskatchewan": "SK", "nova scotia": "NS",
    "new brunswick": "NB", "newfoundland and labrador": "NL",
    "prince edward island": "PE", "northwest territories": "NT",
    "nunavut": "NU", "yukon": "YT",
}
_US_ALIASES = {
    "us", "usa", "u.s.", "u.s.a.", "united states", "united states of america",
    "america", "u.s", "us.",
}
_UK_ALIASES = {
    "uk", "u.k.", "united kingdom", "england", "scotland", "wales",
    "northern ireland", "great britain",
}
_REMOTE_RE = re.compile(
    r"(?i)\b(fully\s+)?remote\b|\bhybrid\b|\bon-?site\b|\bwork from home\b"
    r"|\bwfh\b|\bdistributed\b|\banywhere\b|\bflexible\b|\bvirtual\b"
)
_REMOTE_HINT_RE = re.compile(r"(?i)\bremote\b|\banywhere\b|\bdistributed\b")
_NOISE_RE = re.compile(
    r"(?i)\b(office|campus|hq|headquarters|building|bldg|tower|metro area|"
    r"greater|area|region|site|sez|stpi|downtown|millenia|salarpuria)\b"
)
_ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_LEADING_STREET_RE = re.compile(r"^\d+\s+[\w.\s]+?,\s*")
_MULTI_SPLIT_RE = re.compile(r"\s*(?:;|•|\||\s/\s|\s+and\s+|\s*&\s*)\s*")


@dataclass(frozen=True)
class NormalizedLocation:
    raw: str
    city: str | None
    region: str | None
    country: str | None
    is_remote: bool
    canonical: str

    @property
    def resolved(self) -> bool:
        return bool(self.city or self.region or self.country or self.is_remote)


@lru_cache(maxsize=1)
def _gazetteer():
    gc = geonamescache.GeonamesCache()
    cities = gc.get_cities()
    us_states = gc.get_us_states()
    countries = gc.get_countries()

    state_abbrs = {s["code"] for s in us_states.values()}
    state_name_to_abbr = {s["name"].lower(): s["code"] for s in us_states.values()}
    country_name_to_name = {c["name"].lower(): c["name"] for c in countries.values()}
    country_iso_to_name = {c["iso"].lower(): c["name"] for c in countries.values()}
    iso_to_name = {c["iso"]: c["name"] for c in countries.values()}

    # name (and ascii alternate names) -> list of (name, admin1, cc, population)
    city_index: dict[str, list[tuple[str, str, str, int]]] = defaultdict(list)
    for v in cities.values():
        entry = (v["name"], v.get("admin1code", ""), v["countrycode"], v["population"])
        city_index[v["name"].lower()].append(entry)
        for alt in v.get("alternatenames", []):
            if alt.isascii() and alt.strip():
                city_index[alt.lower()].append(entry)

    return {
        "state_abbrs": state_abbrs,
        "state_name_to_abbr": state_name_to_abbr,
        "country_name_to_name": country_name_to_name,
        "country_iso_to_name": country_iso_to_name,
        "iso_to_name": iso_to_name,
        "city_index": city_index,
    }


def _clean(token: str) -> str:
    token = token.strip().strip("-–—,").strip()
    token = re.sub(r"\s*\(.*?\)\s*", " ", token)
    token = re.sub(r"\s*\[.*?\]\s*", " ", token)
    token = re.sub(r"\s+", " ", token)
    return token.strip()


def split_location_string(value: str) -> list[str]:
    """Split a joined multi-location string into its parts. A value with no
    recognised separator comes back as a one-element list."""
    if not value:
        return []
    parts = [_clean(p) for p in _MULTI_SPLIT_RE.split(value)]
    parts = [p for p in parts if p]
    return parts or [_clean(value)]


def _split_components(text: str) -> list[str]:
    if "," not in text and re.search(r"\s[->]\s|\s--\s", text):
        raw = re.split(r"\s[->]\s|\s--\s", text)
    else:
        raw = re.split(r"\s*[,>]\s*|\s+-\s+", text)
    return [c for c in (_clean(p) for p in raw) if c]


def _resolve(value: str) -> NormalizedLocation:
    g = _gazetteer()
    raw = value
    cleaned = _clean(value)
    is_remote = bool(_REMOTE_HINT_RE.search(value))

    text = _clean(_REMOTE_RE.sub("", cleaned))
    text = _ZIP_RE.sub("", text)
    text = _LEADING_STREET_RE.sub("", text)
    text = re.sub(r"(?i)[-_]\s*(office|hq)\b", "", text)
    if "_" in text and "," not in text:
        text = text.replace("_", ", ")
    text = _clean(text)

    city = region = country = None

    if text:
        parts = _split_components(text)

        # Single bare token: prefer a populous same-named city over the
        # state/country of the same name ("New York" -> NYC, not NY state;
        # but "California" with no city match -> CA state).
        if len(parts) == 1:
            hit = _match_city(parts[0], None, None, g)
            if hit is not None and hit[3] >= 200_000:
                name, adm, cc = hit[0], hit[1], hit[2]
                region = adm if cc == "US" and adm else None
                country = g["iso_to_name"].get(cc)
                canonical = _build_canonical(name, region, country, is_remote, cleaned)
                return NormalizedLocation(raw, name, region, country, is_remote, canonical)

        def is_geo_prefix(p: str) -> bool:
            pl = p.lower()
            return (
                pl in _US_ALIASES or pl in _UK_ALIASES
                or pl in g["country_name_to_name"] or pl in g["country_iso_to_name"]
                or p.upper() in g["state_abbrs"] or pl in g["state_name_to_abbr"]
            )

        if len(parts) >= 2 and is_geo_prefix(parts[0]) and not is_geo_prefix(parts[-1]):
            parts = parts[::-1]

        if parts:
            last = parts[-1]
            ll = last.lower()
            is_subnat = (
                last.upper() in g["state_abbrs"] or ll in g["state_name_to_abbr"]
                or last.upper() in _CANADA_PROVINCES.values() or ll in _CANADA_PROVINCES
                or ll in _CANADA_PROVINCE_NAMES
            )
            if ll in _US_ALIASES:
                country = "United States"; parts.pop()
            elif ll in _UK_ALIASES:
                country = "United Kingdom"; parts.pop()
            elif not is_subnat and ll in g["country_name_to_name"]:
                country = g["country_name_to_name"][ll]; parts.pop()
            elif not is_subnat and ll in g["country_iso_to_name"]:
                country = g["country_iso_to_name"][ll]; parts.pop()

        if parts and country in (None, "United States", "Canada"):
            last = parts[-1]
            ll = last.lower()
            if last.upper() in g["state_abbrs"]:
                region = last.upper(); country = country or "United States"; parts.pop()
            elif ll in g["state_name_to_abbr"]:
                region = g["state_name_to_abbr"][ll]; country = country or "United States"; parts.pop()
            elif last.upper() in _CANADA_PROVINCES.values() or ll in _CANADA_PROVINCES:
                region = _CANADA_PROVINCES.get(ll, last.upper()); country = country or "Canada"; parts.pop()
            elif ll in _CANADA_PROVINCE_NAMES:
                region = _CANADA_PROVINCE_NAMES[ll]; country = country or "Canada"; parts.pop()

        if parts:
            city = _match_city(parts[0], region, country, g)
            if city is not None:
                name, adm, cc = city[0], city[1], city[2]
                city = name
                if not region and cc == "US" and adm:
                    region = adm
                if not country:
                    country = g["iso_to_name"].get(cc)

    canonical = _build_canonical(city, region, country, is_remote, cleaned)
    return NormalizedLocation(raw, city, region, country, is_remote, canonical)


def _match_city(candidate: str, region, country, g):
    idx = g["city_index"]
    key = candidate.lower()
    hits = idx.get(key)
    if not hits:
        stripped = _clean(_NOISE_RE.sub("", candidate))
        if stripped and stripped.lower() != key:
            hits = idx.get(stripped.lower())
    if not hits and " " in candidate:
        words = candidate.split()
        for n in range(len(words) - 1, 0, -1):
            prefix = " ".join(words[:n]).lower()
            if prefix in idx:
                hits = idx[prefix]
                break
    if not hits:
        return None
    if region:
        best = [h for h in hits if h[1] == region] or hits
    elif country:
        cc = {iso for iso, name in g["iso_to_name"].items() if name == country}
        best = [h for h in hits if h[2] in cc] or hits
    else:
        best = hits
    winner = max(best, key=lambda h: h[3])
    return (winner[0], winner[1], winner[2], winner[3])


def _build_canonical(city, region, country, is_remote, cleaned) -> str:
    if city:
        bits = [city]
        if region:
            bits.append(region)
        if country:
            bits.append(country)
        label = ", ".join(bits)
        return f"Remote / {label}" if (is_remote and not city) else label
    if is_remote:
        return f"Remote - {country}" if country else "Remote"
    if region and country:
        return f"{region}, {country}"
    if country:
        return country
    return cleaned or "Unknown"


def normalize_location(value: str | None) -> NormalizedLocation:
    if not value or not value.strip():
        return NormalizedLocation(value or "", None, None, None, False, "Unknown")
    return _resolve(value)
