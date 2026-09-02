"""
Unit tests for GemScraper's response handling - fed real-shaped GraphQL
batch JSON (captured from the live API 2026-09-02), no network.
"""
import json

from scrapy.http import Request, TextResponse

from huntloop.items import JobPostingItem
from huntloop.spiders.gem_spider import GemScraper

ENDPOINT = "https://jobs.gem.com/api/public/graphql/batch"


def _spider(companies=None):
    return GemScraper(companies=companies or ["modular"])


def _response(body, meta):
    req = Request(ENDPOINT, method="POST", meta=meta)
    return TextResponse(req.url, body=json.dumps(body).encode(), request=req, encoding="utf-8")


_LIST_JOB = {
    "id": "T2F0c0pvYlBvc3Q6Mjc0Mjg2Nw==",
    "extId": "4606150005",
    "title": "Libraries Engineer",
    "locations": [{"name": "Mountain View, CA", "city": "Mountain View", "isoCountry": "US", "isRemote": False}],
    "job": {"department": {"name": "Engineering"}, "locationType": "onsite", "employmentType": "FullTime"},
}

_DETAIL = {
    "descriptionHtml": "<p>Build the compiler.</p>",
    "firstPublishedTsSec": 1700000000,
    "compensationHtml": "<p>$150k-$200k</p>",
    "jobPostSectionHtml": {"introHtml": "<p>Intro</p>", "outroHtml": "<p>Outro</p>"},
}


def _list_body(board=True, org="Modular", jobs=None):
    return [{
        "data": {
            "oatsExternalJobPostings": {"jobPostings": jobs if jobs is not None else [_LIST_JOB]},
            "jobBoardExternal": ({"id": "1", "teamDisplayName": org, "pageTitle": f"{org} Careers"} if board else None),
        }
    }]


def test_company_slugs_from_list_and_csv():
    assert _spider(["a", "b"]).slugs == ["a", "b"]
    assert GemScraper(companies="a, b ,c").slugs == ["a", "b", "c"]


def test_parse_list_unknown_slug_is_skipped_not_crashed():
    spider = _spider()
    resp = _response(_list_body(board=False), meta={"slug": "modular"})
    assert list(spider.parse_list(resp)) == []


def test_parse_list_empty_board_is_skipped_not_crashed():
    spider = _spider()
    resp = _response(_list_body(jobs=[]), meta={"slug": "modular"})
    assert list(spider.parse_list(resp)) == []


def test_parse_list_malformed_response_is_skipped_not_crashed():
    spider = _spider()
    resp = _response({"not": "a list"}, meta={"slug": "modular"})
    assert list(spider.parse_list(resp)) == []
    resp2 = _response("not even json but a string wrapped", meta={"slug": "modular"})
    assert list(spider.parse_list(resp2)) == []


def test_parse_list_schedules_one_detail_batch_request():
    spider = _spider()
    resp = _response(_list_body(), meta={"slug": "modular"})
    requests = list(spider.parse_list(resp))
    assert len(requests) == 1
    req = requests[0]
    assert req.url == ENDPOINT
    assert req.method == "POST"
    payload = json.loads(req.body)
    assert len(payload) == 1  # one detail op for the one job
    assert payload[0]["variables"] == {"boardId": "modular", "extId": "4606150005"}
    assert req.meta["ext_ids"] == ["4606150005"]


def test_parse_list_chunks_large_boards():
    spider = _spider()
    many_jobs = [{**_LIST_JOB, "id": f"id{i}", "extId": f"ext{i}"} for i in range(spider.MAX_DETAIL_BATCH + 5)]
    resp = _response(_list_body(jobs=many_jobs), meta={"slug": "modular"})
    requests = list(spider.parse_list(resp))
    assert len(requests) == 2  # 100 + 5, chunked at MAX_DETAIL_BATCH
    assert len(json.loads(requests[0].body)) == spider.MAX_DETAIL_BATCH
    assert len(json.loads(requests[1].body)) == 5


def test_parse_detail_batch_yields_items():
    spider = _spider()
    meta = {"slug": "modular", "ext_ids": ["4606150005"], "jobs_by_ext_id": {"4606150005": _LIST_JOB}}
    body = [{"data": {"oatsExternalJobPosting": _DETAIL}}]
    resp = _response(body, meta=meta)
    out = list(spider.parse_detail_batch(resp))
    assert len(out) == 1
    item = out[0]
    assert isinstance(item, JobPostingItem)
    assert item["job_title"] == "Libraries Engineer"
    assert item["job_url"] == "https://jobs.gem.com/modular/4606150005"
    assert item["job_description"] == "<p>Build the compiler.</p>"
    assert item["company_name"] == "modular"
    assert item["name"] == "gem_api"
    assert item["date_posted"] == "2023-11-14T22:13:20+00:00"
    assert item["department"] == "Engineering"
    assert item["employment_type"] == "FullTime"
    assert item["job_locations"] == ["Mountain View, CA"]
    assert item["job_id"] == f"modular_{_LIST_JOB['id']}"


def test_parse_detail_batch_falls_back_to_section_html_when_no_description():
    spider = _spider()
    meta = {"slug": "modular", "ext_ids": ["4606150005"], "jobs_by_ext_id": {"4606150005": _LIST_JOB}}
    detail_no_desc = {**_DETAIL, "descriptionHtml": None}
    body = [{"data": {"oatsExternalJobPosting": detail_no_desc}}]
    resp = _response(body, meta=meta)
    out = list(spider.parse_detail_batch(resp))
    assert out[0]["job_description"] == "<p>Intro</p>\n<p>Outro</p>"


def test_parse_detail_batch_skips_missing_detail_without_crashing():
    spider = _spider()
    meta = {"slug": "modular", "ext_ids": ["4606150005"], "jobs_by_ext_id": {"4606150005": _LIST_JOB}}
    body = [{"data": {"oatsExternalJobPosting": None}}]
    resp = _response(body, meta=meta)
    assert list(spider.parse_detail_batch(resp)) == []


def test_parse_detail_batch_shape_mismatch_is_skipped_not_crashed():
    spider = _spider()
    meta = {"slug": "modular", "ext_ids": ["a", "b"], "jobs_by_ext_id": {"a": _LIST_JOB, "b": _LIST_JOB}}
    body = [{"data": {"oatsExternalJobPosting": _DETAIL}}]  # only 1 entry for 2 requested
    resp = _response(body, meta=meta)
    assert list(spider.parse_detail_batch(resp)) == []


def test_job_id_namespaced_and_length_capped():
    spider = _spider(["a-very-long-job-board-slug-that-pushes-past-fifty-chars"])
    item = spider._to_item("a-very-long-job-board-slug-that-pushes-past-fifty-chars", _LIST_JOB, _DETAIL)
    assert item["job_id"] == f"gem_{_LIST_JOB['id']}"
    short = spider._to_item("modular", _LIST_JOB, _DETAIL)
    assert short["job_id"] == f"modular_{_LIST_JOB['id']}"


def test_malformed_job_missing_ext_id_is_skipped():
    spider = _spider()
    bad_job = {**_LIST_JOB, "extId": None}
    assert spider._to_item("modular", bad_job, _DETAIL) is None
