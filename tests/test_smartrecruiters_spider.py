"""
Unit tests for SmartRecruitersScraper's response handling - fed
real-shaped Posting API JSON (captured from the live API 2026-08-31),
no network.
"""
import json

from scrapy.http import Request, TextResponse

from huntloop.items import JobPostingItem
from huntloop.spiders.smartrecruiters_spider import SmartRecruitersScraper

API = "https://api.smartrecruiters.com/v1"


def _spider(companies=None):
    return SmartRecruitersScraper(companies=companies or ["hitachisolutions"])


def _list_response(body, offset, company_id="hitachisolutions"):
    req = Request(
        f"{API}/companies/{company_id}/postings?limit=100&offset={offset}",
        meta={"company_id": company_id, "offset": offset},
    )
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


def _detail_response(body, list_posting=None, company_id="hitachisolutions"):
    req = Request(
        f"{API}/companies/{company_id}/postings/{body.get('id', 'x')}",
        meta={"company_id": company_id, "list_posting": list_posting or {}},
    )
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


def test_company_ids_from_list_and_csv():
    assert _spider(["a", "b"]).company_ids == ["a", "b"]
    assert SmartRecruitersScraper(companies="a, b ,c").company_ids == ["a", "b", "c"]


def test_parse_list_fans_out_detail_requests_and_pages():
    spider = _spider()
    body = {
        "totalFound": 250,
        "content": [
            {"id": "111", "name": "A"},
            {"id": "222", "name": "B"},
        ],
    }
    out = list(spider.parse_list(_list_response(body, offset=0)))
    detail = [r for r in out if r.callback == spider.parse_detail]
    pages = [r for r in out if r.callback == spider.parse_list]
    assert [r.url.rsplit("/", 1)[-1] for r in detail] == ["111", "222"]
    # 250 / 100 => remaining pages at offset 100 and 200
    assert sorted(r.meta["offset"] for r in pages) == [100, 200]
    assert detail[0].meta["list_posting"]["name"] == "A"


def test_parse_list_later_page_yields_no_new_pages():
    spider = _spider()
    body = {"totalFound": 250, "content": [{"id": "333", "name": "C"}]}
    out = list(spider.parse_list(_list_response(body, offset=100)))
    assert all(r.callback == spider.parse_detail for r in out)


def test_parse_list_empty_board_is_skipped_not_crashed(caplog):
    spider = _spider()
    body = {"totalFound": 0, "content": []}
    with caplog.at_level("WARNING"):
        out = list(spider.parse_list(_list_response(body, offset=0)))
    assert out == []
    assert "board is empty, skipping" in caplog.text


def test_parse_list_bad_json_yields_nothing():
    spider = _spider()
    req = Request(f"{API}/companies/x/postings?limit=100&offset=0",
                  meta={"company_id": "x", "offset": 0})
    resp = TextResponse(req.url, body=b"<html>nope</html>", request=req, encoding="utf-8")
    assert list(spider.parse_list(resp)) == []


def test_parse_detail_builds_item():
    spider = _spider()
    body = {
        "id": "744000146422469",
        "name": "Senior Data Engineer",
        "refNumber": "REF9",
        "postingUrl": "https://jobs.smartrecruiters.com/hitachisolutions/744000146422469-senior-data-engineer",
        "releasedDate": "2026-08-20T09:14:54.288Z",
        "location": {"city": "Irvine", "region": "CA", "country": "us",
                     "fullLocation": "Irvine, CA, United States", "remote": False},
        "typeOfEmployment": {"label": "Full-time"},
        "department": {"label": "Engineering"},
        "function": {"label": "Information Technology"},
        "jobAd": {"sections": {
            "companyDescription": {"text": "<p>About us boilerplate.</p>"},
            "jobDescription": {"text": "<p>Build data pipelines.</p>"},
            "qualifications": {"text": "<ul><li>Python</li></ul>"},
        }},
    }
    items = list(spider.parse_detail(_detail_response(body)))
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, JobPostingItem)
    assert item["job_title"] == "Senior Data Engineer"
    assert item["job_id"] == "hitachisolutions_744000146422469"   # namespaced
    assert item["name"] == "smartrecruiters_api"
    assert item["company_name"] == "hitachisolutions"
    assert item["job_url"].endswith("744000146422469-senior-data-engineer")
    assert item["date_posted"] == "2026-08-20T09:14:54.288Z"
    assert item["job_locations"] == ["Irvine, CA, United States"]
    assert item["employment_type"] == "Full-time"
    assert item["department"] == "Engineering"
    # description joins jobDescription + qualifications, drops the shared companyDescription
    assert "Build data pipelines" in item["job_description"]
    assert "Python" in item["job_description"]
    assert "boilerplate" not in item["job_description"]
    assert json.loads(item["metadata_json"])["refNumber"] == "REF9"


def test_parse_detail_missing_id_yields_nothing():
    spider = _spider()
    assert list(spider.parse_detail(_detail_response({"name": "no id"}))) == []


def test_parse_detail_falls_back_to_list_posting_fields():
    spider = _spider()
    list_posting = {"id": "999", "name": "Fallback Title",
                    "location": {"fullLocation": "Remote"},
                    "releasedDate": "2026-01-01T00:00:00.000Z"}
    body = {"id": "999"}  # detail with almost nothing
    item = list(spider.parse_detail(_detail_response(body, list_posting=list_posting)))[0]
    assert item["job_title"] == "Fallback Title"
    assert item["job_locations"] == ["Remote"]
    assert item["date_posted"] == "2026-01-01T00:00:00.000Z"
    assert item["job_description"] is None


def test_parse_detail_long_companyid_uses_sr_prefix_to_fit_varchar50():
    spider = SmartRecruitersScraper(companies=["internationalqualityhomecarecorporation"])
    body = {"id": "743999659167870", "name": "Personal Care Assistant",
            "postingUrl": "https://jobs.smartrecruiters.com/x/743999659167870"}
    req = Request(f"{API}/companies/internationalqualityhomecarecorporation/postings/743999659167870",
                  meta={"company_id": "internationalqualityhomecarecorporation", "list_posting": {}})
    resp = TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")
    item = list(spider.parse_detail(resp))[0]
    assert item["job_id"] == "sr_743999659167870"
    assert len(item["job_id"]) <= 50
