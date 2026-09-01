"""
Unit tests for AshbyScraper's response handling - fed real-shaped Job
Board API JSON (captured from the live API 2026-09-01), no network.
"""
import json

from scrapy.http import Request, TextResponse

from huntloop.items import JobPostingItem
from huntloop.spiders.ashby_spider import AshbyScraper

API = "https://api.ashbyhq.com/posting-api/job-board"


def _spider(companies=None):
    return AshbyScraper(companies=companies or ["linear"])


def _board_response(body, slug="linear"):
    req = Request(f"{API}/{slug}", meta={"slug": slug})
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


_JOB = {
    "id": "d3bc1ced-3ce4-4086-a050-555055dbb1ff",
    "title": "Senior / Staff Fullstack Engineer",
    "department": "Product",
    "team": "Engineering",
    "employmentType": "FullTime",
    "location": "Europe",
    "secondaryLocations": [{"location": "North America"}],
    "publishedAt": "2021-04-27T20:13:45.158+00:00",
    "isListed": True,
    "isRemote": True,
    "workplaceType": "Remote",
    "jobUrl": "https://jobs.ashbyhq.com/linear/d3bc1ced-3ce4-4086-a050-555055dbb1ff",
    "applyUrl": "https://jobs.ashbyhq.com/linear/d3bc1ced-3ce4-4086-a050-555055dbb1ff/application",
    "descriptionHtml": "<p>At Linear, we're building the product development system.</p>",
    "descriptionPlain": "At Linear, we're building the product development system.",
}


def test_company_slugs_from_list_and_csv():
    assert _spider(["a", "b"]).job_board_names == ["a", "b"]
    assert AshbyScraper(companies="a, b ,c").job_board_names == ["a", "b", "c"]


def test_parse_board_yields_items_no_pagination():
    spider = _spider()
    out = list(spider.parse_board(_board_response({"jobs": [_JOB, {**_JOB, "id": "x2"}], "apiVersion": "1"})))
    assert all(isinstance(i, JobPostingItem) for i in out)
    assert len(out) == 2  # one item per job, no follow-up requests
    item = out[0]
    assert item["job_title"] == "Senior / Staff Fullstack Engineer"
    assert item["job_url"] == _JOB["jobUrl"]
    assert item["job_description"] == _JOB["descriptionHtml"]
    assert item["company_name"] == "linear"
    assert item["name"] == "ashby_api"
    assert item["date_posted"] == "2021-04-27T20:13:45.158+00:00"
    assert item["job_locations"] == ["Europe", "North America"]
    assert item["department"] == "Product"


def test_job_id_namespaced_and_length_capped():
    spider = _spider(["a-very-long-job-board-slug-here"])
    item = spider._to_item("a-very-long-job-board-slug-here", _JOB)
    # "{slug}_{uuid}" would exceed varchar(50) -> falls back to "ashby_{uuid}"
    assert item["job_id"] == f"ashby_{_JOB['id']}"
    short = spider._to_item("linear", _JOB)
    assert short["job_id"] == f"linear_{_JOB['id']}"


def test_empty_board_is_skipped_not_crashed():
    spider = _spider()
    assert list(spider.parse_board(_board_response({"jobs": [], "apiVersion": "1"}))) == []


def test_missing_jobs_key_is_skipped():
    spider = _spider()
    assert list(spider.parse_board(_board_response({"apiVersion": "1"}))) == []


def test_non_json_response_is_skipped():
    spider = _spider()
    req = Request(f"{API}/linear", meta={"slug": "linear"})
    resp = TextResponse(req.url, body=b"Not Found", request=req, encoding="utf-8")
    assert list(spider.parse_board(resp)) == []


def test_malformed_job_missing_url_is_dropped():
    spider = _spider()
    bad = {k: v for k, v in _JOB.items() if k not in ("jobUrl", "applyUrl")}
    out = list(spider.parse_board(_board_response({"jobs": [bad], "apiVersion": "1"})))
    assert out == []
