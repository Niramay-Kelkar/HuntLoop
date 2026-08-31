"""
Unit tests for WorkdayScraper's response handling - fed real-shaped CXS
JSON (captured from live tenants 2026-08-30), no network.
"""
import json

from scrapy.http import Request, TextResponse

from huntloop.items import JobPostingItem
from huntloop.spiders.workday_spider import WorkdayScraper

CXS = "https://nxp.wd3.myworkdayjobs.com/wday/cxs/nxp/careers"


def _spider():
    return WorkdayScraper(careers_urls={"nxp": "https://nxp.wd3.myworkdayjobs.com/en-US/careers"})


def _list_response(body, offset):
    req = Request(f"{CXS}/jobs", method="POST",
                  meta={"company_name": "nxp", "cxs_base": CXS, "tenant": "nxp", "offset": offset})
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


def _detail_response(body, list_posted_on=None):
    req = Request(f"{CXS}/job/Somewhere/Some-Job_R1",
                  meta={"company_name": "nxp", "tenant": "nxp", "list_posted_on": list_posted_on})
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


def test_targets_parses_careers_url():
    spider = _spider()
    assert list(spider._targets()) == [("nxp", "nxp", "wd3", "careers")]


def test_targets_skips_company_without_careers_url(caplog):
    spider = WorkdayScraper(companies=["nxp", "ghost"],
                            careers_urls={"nxp": "https://nxp.wd3.myworkdayjobs.com/en-US/careers"})
    with caplog.at_level("WARNING"):
        targets = list(spider._targets())
    assert targets == [("nxp", "nxp", "wd3", "careers")]
    assert "no careers_url for 'ghost'" in caplog.text


def test_parse_list_fans_out_detail_requests_and_pages():
    spider = _spider()
    body = {"total": 45, "jobPostings": [
        {"title": "A", "externalPath": "/job/x/A_R1", "postedOn": "Posted 2 Days Ago"},
        {"title": "B", "externalPath": "/job/x/B_R2", "postedOn": "Posted Today"},
    ]}
    out = list(spider.parse_list(_list_response(body, offset=0)))
    detail = [r for r in out if r.callback == spider.parse_detail]
    pages = [r for r in out if r.callback == spider.parse_list]
    assert [r.url for r in detail] == [f"{CXS}/job/x/A_R1", f"{CXS}/job/x/B_R2"]
    # 45 postings / 20 per page => pages at offset 20 and 40 (offset 0 already done)
    assert sorted(r.meta["offset"] for r in pages) == [20, 40]
    assert detail[0].meta["list_posted_on"] == "Posted 2 Days Ago"


def test_parse_list_later_page_yields_no_new_pages():
    spider = _spider()
    body = {"total": 45, "jobPostings": [{"title": "C", "externalPath": "/job/x/C_R3"}]}
    out = list(spider.parse_list(_list_response(body, offset=20)))
    assert all(r.callback == spider.parse_detail for r in out)


def test_parse_list_bad_json_increments_error_metric_and_yields_nothing():
    spider = _spider()
    req = Request(f"{CXS}/jobs", method="POST",
                  meta={"company_name": "nxp", "cxs_base": CXS, "tenant": "nxp", "offset": 0})
    resp = TextResponse(req.url, body=b"<html>nope</html>", request=req, encoding="utf-8")
    assert list(spider.parse_list(resp)) == []


def test_parse_detail_builds_item_with_absolute_date():
    spider = _spider()
    body = {"jobPostingInfo": {
        "title": "Lead Physical Design Engineer",
        "jobDescription": "<p>Design chips.</p>",
        "location": "Pune",
        "additionalLocations": ["Eindhoven"],
        "startDate": "2026-08-20",
        "postedOn": "Posted 10 Days Ago",
        "timeType": "Full time",
        "jobReqId": "R-10066075",
        "externalUrl": "https://nxp.wd3.myworkdayjobs.com/careers/job/Pune/Lead_R-10066075",
    }}
    items = list(spider.parse_detail(_detail_response(body)))
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, JobPostingItem)
    assert item["job_title"] == "Lead Physical Design Engineer"
    assert item["job_url"].endswith("Lead_R-10066075")
    assert item["job_id"] == "nxp_R-10066075"          # namespaced
    assert item["name"] == "workday_api"
    assert item["date_posted"] == "2026-08-20T00:00:00+00:00"   # startDate, absolute
    assert item["job_locations"] == ["Pune", "Eindhoven"]
    assert item["employment_type"] == "Full time"
    assert json.loads(item["metadata_json"])["jobReqId"] == "R-10066075"


def test_parse_detail_falls_back_to_relative_date_when_no_start_date():
    spider = _spider()
    body = {"jobPostingInfo": {
        "title": "X", "jobDescription": "", "startDate": None,
        "postedOn": "Posted 3 Days Ago", "jobReqId": "R9",
        "externalUrl": "https://nxp.wd3.myworkdayjobs.com/careers/job/X_R9",
    }}
    item = list(spider.parse_detail(_detail_response(body)))[0]
    assert item["date_posted"].startswith("20")  # a real ISO date, not None
    assert "T00:00:00" in item["date_posted"]


def test_parse_detail_missing_jobPostingInfo_yields_nothing():
    spider = _spider()
    assert list(spider.parse_detail(_detail_response({"error": "gone"}))) == []
