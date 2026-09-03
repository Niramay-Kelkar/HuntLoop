"""
Unit tests for IcimsScraper's response handling - fed real-shaped iCIMS
portal HTML, no network.
"""
from scrapy.http import Request, TextResponse

from huntloop.items import JobPostingItem
from huntloop.spiders.icims_spider import USER_AGENT, IcimsScraper

BASE = "https://careers-herbalife.icims.com"


def _spider(companies=None):
    return IcimsScraper(companies=companies or ["herbalife"])


def _resp(url, body, status=200, meta=None):
    req = Request(url, meta=meta or {})
    return TextResponse(url, body=body.encode(), status=status, request=req, encoding="utf-8")


ROBOTS_OK = "User-agent: *\nDisallow: /connect\nDisallow: /jobs/login\n"
ROBOTS_BLOCK = "User-agent: *\nDisallow: /\n"

LISTING_P0 = f"""<html><head><title>Job Listings at Herbalife</title>
<link rel="next" href="{BASE}/jobs/search?pr=1&amp;in_iframe=1" /></head><body>
<a href="{BASE}/jobs/20711/mgr/job?in_iframe=1" class="iCIMS_Anchor" title="20711 - HR Manager">HR Manager</a>
<a href="{BASE}/jobs/20679/eng/job?in_iframe=1" class="iCIMS_Anchor" title="20679 - Data Engineer">Data Engineer</a>
<div>Page 1 of 2</div></body></html>"""

LISTING_P1 = f"""<html><head><title>Job Listings at Herbalife</title></head><body>
<a href="{BASE}/jobs/20001/x/job?in_iframe=1" class="iCIMS_Anchor" title="20001 - Analyst">Analyst</a>
<div>Page 2 of 2</div></body></html>"""

LISTING_EMPTY = """<html><head><title>Job Listings at Insmed Incorporated</title></head>
<body>Sorry, no jobs were found that match your search criteria.</body></html>"""

JOB_JSONLD = f"""<html><head>
<script type="application/ld+json">
{{"@type":"JobPosting","title":"Data Engineer","datePosted":"2026-08-20",
 "employmentType":"FULL_TIME","occupationalCategory":"Tech",
 "hiringOrganization":{{"name":"Herbalife"}},
 "jobLocation":{{"@type":"Place","address":{{"addressLocality":"Winston-Salem","addressRegion":"NC","addressCountry":"US"}}}},
 "description":"<p>Build pipelines.</p>","url":"{BASE}/jobs/20679/eng/job"}}
</script></head><body><h1 class="iCIMS_Header">Data Engineer</h1></body></html>"""

JOB_NO_JSONLD = f"""<html><head>
<meta property="og:title" content="Analyst in Los Angeles, CA | Careers at Herbalife">
<meta name="description" content="Crunch numbers.">
</head><body><h1 class="iCIMS_Header">Analyst</h1>
<div class="iCIMS_Expandable_Text"><p>Do analysis.</p></div></body></html>"""


# --- robots gate ---

def test_robots_block_skips_tenant(caplog):
    spider = _spider()
    resp = _resp(f"{BASE}/robots.txt", ROBOTS_BLOCK, meta={"slug": "herbalife"})
    with caplog.at_level("WARNING"):
        out = list(spider.parse_robots(resp))
    assert out == []
    assert "robots.txt disallows" in caplog.text


def test_robots_ok_starts_listing():
    spider = _spider()
    resp = _resp(f"{BASE}/robots.txt", ROBOTS_OK, meta={"slug": "herbalife"})
    out = list(spider.parse_robots(resp))
    assert len(out) == 1 and out[0].url == f"{BASE}/jobs/search?pr=0&in_iframe=1"


def test_missing_robots_treated_permissive():
    spider = _spider()
    resp = _resp(f"{BASE}/robots.txt", "", status=404, meta={"slug": "herbalife"})
    out = list(spider.parse_robots(resp))
    assert len(out) == 1  # 404 robots -> allowed


# --- listing ---

def test_parse_listing_fans_out_jobs_and_next_page():
    spider = _spider()
    resp = _resp(f"{BASE}/jobs/search?pr=0&in_iframe=1", LISTING_P0, meta={"slug": "herbalife", "page": 0})
    out = list(spider.parse_listing(resp))
    job_reqs = [r for r in out if r.callback == spider.parse_job]
    page_reqs = [r for r in out if r.callback == spider.parse_listing]
    assert len(job_reqs) == 2
    assert job_reqs[0].url == f"{BASE}/jobs/20711/mgr/job?in_iframe=1"
    assert job_reqs[0].meta["title_hint"] == "HR Manager"
    assert len(page_reqs) == 1 and page_reqs[0].meta["page"] == 1


def test_parse_listing_last_page_stops_pagination():
    spider = _spider()
    resp = _resp(f"{BASE}/jobs/search?pr=1&in_iframe=1", LISTING_P1, meta={"slug": "herbalife", "page": 1})
    out = list(spider.parse_listing(resp))
    assert [r for r in out if r.callback == spider.parse_listing] == []  # "Page 2 of 2", no next link


def test_parse_listing_no_openings_is_graceful(caplog):
    spider = _spider(["insmed"])
    resp = _resp(f"{BASE}/jobs/search?pr=0&in_iframe=1", LISTING_EMPTY, meta={"slug": "insmed", "page": 0})
    with caplog.at_level("INFO"):
        assert list(spider.parse_listing(resp)) == []
    assert "no open jobs" in caplog.text


def test_parse_listing_http_error_does_not_crash():
    spider = _spider()
    resp = _resp(f"{BASE}/jobs/search?pr=0&in_iframe=1", "boom", status=503,
                 meta={"slug": "herbalife", "page": 0})
    assert list(spider.parse_listing(resp)) == []


# --- job detail ---

def test_parse_job_jsonld_primary():
    spider = _spider()
    meta = {"slug": "herbalife", "job_id": "20679",
            "canonical_url": f"{BASE}/jobs/20679/eng/job", "title_hint": "Data Engineer"}
    resp = _resp(meta["canonical_url"] + "?in_iframe=1", JOB_JSONLD, meta=meta)
    items = list(spider.parse_job(resp))
    assert len(items) == 1 and isinstance(items[0], JobPostingItem)
    it = items[0]
    assert it["job_id"] == "herbalife_20679"
    assert it["job_title"] == "Data Engineer"
    assert it["job_url"] == f"{BASE}/jobs/20679/eng/job"
    assert it["job_description"] == "<p>Build pipelines.</p>"
    assert it["company_name"] == "herbalife"
    assert it["name"] == "icims_portal"
    assert it["date_posted"] == "2026-08-20"
    assert it["job_locations"] == ["Winston-Salem, NC, US"]
    assert spider._jsonld_ok == 1 and spider._html_fallback_jobs == 0


def test_parse_job_html_fallback_when_no_jsonld(caplog):
    spider = _spider()
    meta = {"slug": "herbalife", "job_id": "20001",
            "canonical_url": f"{BASE}/jobs/20001/x/job", "title_hint": "Analyst"}
    resp = _resp(meta["canonical_url"] + "?in_iframe=1", JOB_NO_JSONLD, meta=meta)
    with caplog.at_level("WARNING"):
        items = list(spider.parse_job(resp))
    assert len(items) == 1
    it = items[0]
    assert it["job_title"] == "Analyst"
    assert it["job_locations"] == ["Los Angeles, CA"]
    assert "Do analysis." in it["job_description"]
    assert it["date_posted"] is None  # no absolute date outside JSON-LD - left NULL
    assert spider._html_fallback_jobs == 1
    assert "no JSON-LD JobPosting block" in caplog.text


def test_parse_job_http_error_skips_job():
    spider = _spider()
    meta = {"slug": "herbalife", "job_id": "9", "canonical_url": f"{BASE}/jobs/9/x/job", "title_hint": None}
    resp = _resp(f"{BASE}/jobs/9/x/job?in_iframe=1", "nope", status=500, meta=meta)
    assert list(spider.parse_job(resp)) == []


def test_job_id_namespaced_length_cap():
    spider = _spider(["a-very-very-very-long-icims-subdomain-slug-here"])
    it = spider._to_item("a-very-very-very-long-icims-subdomain-slug-here", "12345",
                         "https://x/jobs/12345/t/job", {"title": "T", "url": None}, "json-ld")
    assert it["job_id"] == "icims_12345"


def test_companies_arg_from_csv():
    assert IcimsScraper(companies="a, b ,c").slugs == ["a", "b", "c"]


def test_user_agent_is_honest_not_a_browser():
    assert "HuntLoop" in USER_AGENT and "Mozilla" not in USER_AGENT
    assert IcimsScraper.custom_settings["ROBOTSTXT_OBEY"] is True
