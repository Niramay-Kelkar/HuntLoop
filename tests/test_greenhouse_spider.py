"""
Unit test for GreenhouseScraper's department extraction - fed real-shaped
Boards API JSON (captured live from api.greenhouse.io 2026-09-02), no
network. The real per-job `departments` list (not the free-form `metadata`
array) is Greenhouse's actual department field - see CLAUDE.md.
"""
import json

from scrapy.http import Request, TextResponse

from huntloop.spiders.greenhouse_spider import GreenhouseScraper

BASE = "https://api.greenhouse.io/v1/boards/testco/jobs"

_JOB = {
    "id": 12345,
    "title": "Software Engineer",
    "absolute_url": "https://boards.greenhouse.io/testco/jobs/12345",
    "first_published": "2026-01-01T00:00:00-05:00",
    "content": "<p>Do things.</p>",
    "offices": [{"name": "Remote"}],
    "location": {"name": "Remote"},
    "departments": [{"id": 1, "name": "Engineering", "child_ids": [], "parent_id": None}],
    "metadata": [{"id": 1, "name": "Career Site Category", "value": "Sales", "value_type": "single_select"}],
}


def _response(body, token="testco"):
    req = Request(BASE, meta={"company_token": token})
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


def test_department_extracted_from_real_departments_field():
    spider = GreenhouseScraper(companies=["testco"])
    items = list(spider.parse(_response({"jobs": [_JOB]})))
    assert len(items) == 1
    assert items[0]["department"] == "Engineering"


def test_metadata_department_used_only_as_fallback():
    job = {**_JOB, "departments": []}
    spider = GreenhouseScraper(companies=["testco"])
    items = list(spider.parse(_response({"jobs": [job]})))
    # No structured `departments` entry and no metadata entry named
    # "department" - stays unset (not the unrelated "Career Site Category").
    assert items[0].get("department") is None


def test_metadata_department_fallback_when_named_department():
    job = {
        **_JOB,
        "departments": [],
        "metadata": [{"id": 1, "name": "Department", "value": "Support", "value_type": "single_select"}],
    }
    spider = GreenhouseScraper(companies=["testco"])
    items = list(spider.parse(_response({"jobs": [job]})))
    assert items[0]["department"] == "Support"
