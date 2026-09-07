"""Tests for huntloop.location_normalization - real sample values across
the frequency spectrum, joined multi-location strings, and genuinely
unresolvable values."""

import pytest

from huntloop.location_normalization import (
    normalize_location,
    split_location_string,
)


@pytest.mark.parametrize(
    "raw, city, region, country",
    [
        ("San Francisco", "San Francisco", "CA", "United States"),
        ("San Francisco, CA", "San Francisco", "CA", "United States"),
        ("San Francisco, California, United States", "San Francisco", "CA", "United States"),
        ("San Francisco Bay Area", "San Francisco", "CA", "United States"),
        ("New York", "New York City", "NY", "United States"),
        ("New York, NY", "New York City", "NY", "United States"),
        ("Portland, OR, US", "Portland", "OR", "United States"),
        ("North Chicago, IL, United States", "North Chicago", "IL", "United States"),
        ("London, United Kingdom", "London", None, "United Kingdom"),
        ("Bengaluru, KA, India", "Bengaluru", None, "India"),
        ("Toronto, ON", "Toronto", "ON", "Canada"),
        ("California - San Francisco", "San Francisco", "CA", "United States"),
        ("US > Arizona > Phoenix", "Phoenix", "AZ", "United States"),
        ("Bengaluru Millenia", "Bengaluru", None, "India"),
    ],
)
def test_resolves_city_region_country(raw, city, region, country):
    n = normalize_location(raw)
    assert (n.city, n.region, n.country) == (city, region, country)
    assert n.resolved


def test_all_san_francisco_variants_share_one_canonical_label():
    variants = [
        "San Francisco",
        "San Francisco, CA",
        "San Francisco, California",
        "San Francisco, California, United States",
        "San Francisco, CA, US",
        "SF",
        "San Francisco Bay Area",
    ]
    labels = {normalize_location(v).canonical for v in variants}
    assert labels == {"San Francisco, CA, United States"}


@pytest.mark.parametrize(
    "raw",
    ["Remote", "Remote - United States", "Remote, US", "US - Remote", "Fully Remote"],
)
def test_remote_is_flagged(raw):
    assert normalize_location(raw).is_remote is True


def test_remote_with_country_keeps_country():
    n = normalize_location("Remote - United States")
    assert n.is_remote is True
    assert n.country == "United States"
    assert n.canonical == "Remote - United States"


def test_country_only_value():
    n = normalize_location("United States")
    assert (n.city, n.country) == (None, "United States")
    assert n.canonical == "United States"


@pytest.mark.parametrize("raw", ["Hybrid", "HQ", "Software Engineering", "N/A", "Site"])
def test_unresolvable_values_fall_back_to_raw(raw):
    n = normalize_location(raw)
    assert not n.resolved
    assert n.city is None and n.country is None
    assert n.canonical  # never empty - the cleaned raw string


def test_empty_and_none():
    assert normalize_location("").canonical == "Unknown"
    assert normalize_location(None).canonical == "Unknown"
    assert not normalize_location(None).resolved


@pytest.mark.parametrize(
    "raw, parts",
    [
        ("San Francisco, CA; New York, NY", ["San Francisco, CA", "New York, NY"]),
        ("San Francisco, CA • New York, NY • United States",
         ["San Francisco, CA", "New York, NY", "United States"]),
        ("Livingston, NJ / New York, NY / Sunnyvale, CA",
         ["Livingston, NJ", "New York, NY", "Sunnyvale, CA"]),
        ("Kyiv; Lviv; Remote", ["Kyiv", "Lviv", "Remote"]),
        ("San Francisco, CA", ["San Francisco, CA"]),  # no separator -> one part
        ("Chicago", ["Chicago"]),
    ],
)
def test_split_location_string(raw, parts):
    assert split_location_string(raw) == parts


def test_split_then_normalize_each_piece():
    pieces = split_location_string("San Francisco, CA; New York, NY")
    labels = [normalize_location(p).canonical for p in pieces]
    assert labels == [
        "San Francisco, CA, United States",
        "New York City, NY, United States",
    ]


def test_non_us_two_letter_subnational_code_is_not_a_us_state():
    # "TN" is Tamil Nadu here, not Tennessee - left unset rather than
    # mis-parsed, but the country still resolves.
    n = normalize_location("Chennai, TN, India")
    assert n.country == "India"
    assert n.region is None
