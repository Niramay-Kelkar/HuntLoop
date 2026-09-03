"""
Unit tests for huntloop.workday_url - the pure URL-parsing and
date-normalization helpers behind the Workday spider. No network.
"""
from datetime import datetime, timezone

import pytest

from huntloop.workday_url import (
    build_cxs_base,
    normalize_workday_date,
    parse_workday_careers_url,
)

NOW = datetime(2026, 8, 30, 14, 0, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("url,expected", [
    ("https://nxp.wd3.myworkdayjobs.com/en-US/careers", ("nxp", "wd3", "careers")),
    ("https://regeneron.wd1.myworkdayjobs.com/Careers", ("regeneron", "wd1", "Careers")),
    ("https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site",
     ("salesforce", "wd12", "External_Career_Site")),
    ("https://adobe.wd5.myworkdayjobs.com/en-US/external_experienced",
     ("adobe", "wd5", "external_experienced")),
    # trailing job path is ignored
    ("https://cdw.wd5.myworkdayjobs.com/Careers/job/Chicago/Analyst_R123",
     ("cdw", "wd5", "Careers")),
    # no locale segment
    ("https://organon.wd5.myworkdayjobs.com/searchjobs", ("organon", "wd5", "searchjobs")),
])
def test_parse_careers_url(url, expected):
    assert parse_workday_careers_url(url) == expected


@pytest.mark.parametrize("bad", [
    "",
    "https://boards.greenhouse.io/nxp",
    "https://jobs.lever.co/palantir",
    "https://nxp.myworkdayjobs.com/careers",          # missing wd{N}
    "https://nxp.wd3.myworkdayjobs.com/",             # no site segment
    "not a url",
])
def test_parse_careers_url_rejects_non_workday(bad):
    with pytest.raises(ValueError):
        parse_workday_careers_url(bad)


def test_build_cxs_base():
    assert build_cxs_base("nxp", "wd3", "careers") == (
        "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers"
    )


def test_start_date_is_source_of_truth():
    # absolute startDate wins even when postedOn disagrees
    assert normalize_workday_date("2026-08-20", "Posted 3 Days Ago", now=NOW) == (
        "2026-08-20T00:00:00+00:00"
    )


@pytest.mark.parametrize("posted_on,expected_date", [
    ("Posted Today", "2026-08-30"),
    ("Posted Yesterday", "2026-08-29"),
    ("Posted 2 Days Ago", "2026-08-28"),
    ("Posted 10 Days Ago", "2026-08-20"),
    ("Posted 30 Days Ago", "2026-07-31"),
    ("Posted 30+ Days Ago", "2026-07-31"),   # "30+" floored to 30
])
def test_relative_posted_on_fallback(posted_on, expected_date):
    got = normalize_workday_date(None, posted_on, now=NOW)
    assert got == f"{expected_date}T00:00:00+00:00"


@pytest.mark.parametrize("start_date,posted_on", [
    (None, None),
    (None, "Posted recently"),
    ("garbage", None),
    ("", ""),
])
def test_no_date_available_returns_none(start_date, posted_on):
    assert normalize_workday_date(start_date, posted_on, now=NOW) is None


def test_malformed_start_date_falls_back_to_relative():
    assert normalize_workday_date("2026/08/20", "Posted 2 Days Ago", now=NOW) == (
        "2026-08-28T00:00:00+00:00"
    )
