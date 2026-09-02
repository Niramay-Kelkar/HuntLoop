"""
Unit tests for huntloop.icims_portal - the pure HTML/JSON-LD/robots
parsing behind the iCIMS spider. Fed real-shaped markup captured from
live iCIMS tenants 2026-09-01. No network.
"""
from huntloop.icims_portal import (
    careers_base_url,
    extract_jobposting_jsonld,
    find_next_page_url,
    html_description,
    html_locations,
    html_title,
    jobposting_fields,
    listing_has_no_results,
    listing_org_name,
    listing_url,
    parse_listing_anchors,
    parse_page_indicator,
    robots_allows_listing,
    strip_query,
    with_in_iframe,
)

UA = "HuntLoop/1.0 (sponsorship-aware job aggregator; sponsorship-matching research)"

LISTING = """<!doctype html><html><head>
<title>Job Listings at Herbalife</title>
<link rel="next" href="https://careers-herbalife.icims.com/jobs/search?pr=1&amp;in_iframe=1" />
</head><body>
<table class="iCIMS_JobsTable">
<tr><td><a href="https://careers-herbalife.icims.com/jobs/20711/sr.-manager%2c-hr-business-partner/job?in_iframe=1"
  class="iCIMS_Anchor" title="20711 - Sr. Manager, HR Business Partner">Sr. Manager, HR Business Partner</a></td></tr>
<tr><td><a href="https://careers-herbalife.icims.com/jobs/20679/principal-i%2c-fusion/job?in_iframe=1"
  class="iCIMS_Anchor" title="20679 - Principal I, Fusion Data Intelligence, Oracle">Principal I</a></td></tr>
<tr><td><a href="https://careers-herbalife.icims.com/jobs/20711/sr.-manager%2c-hr-business-partner/job?foo=1"
  class="iCIMS_Anchor" title="20711 - dup">dup row</a></td></tr>
</table>
<div class="iCIMS_Paging">Page 1 of 3</div>
</body></html>"""

NO_RESULTS = """<html><head><title>Job Listings at Insmed Incorporated</title></head>
<body>Sorry, no jobs were found that match your search criteria.</body></html>"""

JSONLD_JOB = """<html><head><meta property="og:title" content="Principal I, Fusion in Winston-Salem, NC | Careers at X">
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"JobPosting","title":"Principal I, Fusion Data Intelligence, Oracle",
 "datePosted":"2026-08-20T04:00:00.000Z","validThrough":"2027-08-20T04:00:00.000Z","employmentType":"FULL_TIME",
 "occupationalCategory":"Global Technology Services",
 "hiringOrganization":{"@type":"Organization","name":"Herbalife"},
 "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Winston-Salem",
   "addressRegion":"NC","addressCountry":"US","postOfficeBoxNumber":"UNAVAILABLE"}}],
 "description":"<h2>Overview</h2><p>Lead analytics.</p>",
 "url":"https://careers-herbalife.icims.com/jobs/20679/principal-i/job"}
</script></head><body>
<h1 class="iCIMS_Header">Principal I, Fusion Data Intelligence, Oracle</h1>
<div class="iCIMS_Expandable_Text"><p>Lead analytics work here.</p></div>
</body></html>"""

GRAPH_JOB = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
  {"@type":"WebSite","name":"Careers"},
  {"@type":["JobPosting"],"title":"Data Engineer","description":"<p>ETL.</p>",
   "url":"https://careers-x.icims.com/jobs/5/data-engineer/job"}
]}</script></head><body></body></html>"""

NO_JSONLD_JOB = """<html><head>
<meta property="og:title" content="Field Sales Rep in Dallas, TX | Careers at Y">
<meta name="description" content="Sell things in Dallas.">
</head><body>
<h1 class="iCIMS_Header">Field Sales Representative</h1>
<div class="iCIMS_Expandable_Text"><p>Visit clients.</p></div>
<div class="iCIMS_Expandable_Text"><p>Hit quota.</p></div>
</body></html>"""


def test_url_builders():
    assert careers_base_url("herbalife") == "https://careers-herbalife.icims.com"
    assert listing_url("herbalife", 2) == "https://careers-herbalife.icims.com/jobs/search?pr=2&in_iframe=1"
    assert strip_query("https://x/jobs/1/a/job?in_iframe=1") == "https://x/jobs/1/a/job"
    assert with_in_iframe("https://x/jobs/1/a/job?foo=1") == "https://x/jobs/1/a/job?in_iframe=1"


def test_parse_listing_anchors_dedups_and_strips_title_prefix():
    rows = parse_listing_anchors(LISTING)
    assert [r["job_id"] for r in rows] == ["20711", "20679"]  # dup id dropped
    assert rows[0]["url"] == "https://careers-herbalife.icims.com/jobs/20711/sr.-manager%2c-hr-business-partner/job"
    assert rows[0]["title_hint"] == "Sr. Manager, HR Business Partner"  # "20711 - " stripped


def test_pagination_signals():
    assert find_next_page_url(LISTING, "https://careers-herbalife.icims.com/jobs/search?pr=0&in_iframe=1") == \
        "https://careers-herbalife.icims.com/jobs/search?pr=1&in_iframe=1"
    assert parse_page_indicator(LISTING) == (1, 3)
    assert find_next_page_url(NO_RESULTS, "https://x") is None
    assert parse_page_indicator("no indicator here") is None


def test_no_results_and_org_name():
    assert listing_has_no_results(NO_RESULTS) is True
    assert listing_has_no_results(LISTING) is False
    assert listing_org_name(LISTING) == "Herbalife"
    assert listing_org_name(NO_RESULTS) == "Insmed Incorporated"


def test_extract_jsonld_flat_and_graph():
    flat = extract_jobposting_jsonld(JSONLD_JOB)
    assert flat and flat["title"] == "Principal I, Fusion Data Intelligence, Oracle"
    graph = extract_jobposting_jsonld(GRAPH_JOB)
    assert graph and graph["title"] == "Data Engineer"  # found inside @graph, @type as list
    assert extract_jobposting_jsonld(NO_JSONLD_JOB) is None


def test_jobposting_fields_normalization():
    f = jobposting_fields(extract_jobposting_jsonld(JSONLD_JOB))
    assert f["title"] == "Principal I, Fusion Data Intelligence, Oracle"
    assert f["date_posted"] == "2026-08-20T04:00:00.000Z"
    assert f["employment_type"] == "FULL_TIME"
    assert f["department"] == "Global Technology Services"
    assert f["locations"] == ["Winston-Salem, NC, US"]  # UNAVAILABLE PO box dropped
    assert f["hiring_organization"] == "Herbalife"
    assert f["url"].endswith("/jobs/20679/principal-i/job")


def test_html_fallback_helpers():
    assert html_title(NO_JSONLD_JOB) == "Field Sales Representative"
    assert html_locations(NO_JSONLD_JOB) == ["Dallas, TX"]
    desc = html_description(NO_JSONLD_JOB)
    assert "Visit clients." in desc and "Hit quota." in desc


def test_robots_allows_listing():
    assert robots_allows_listing("User-agent: *\nDisallow: /connect\nDisallow: /jobs/login", UA) is True
    assert robots_allows_listing("User-agent: *\nDisallow: /", UA) is False
    assert robots_allows_listing("User-agent: *\nDisallow: /jobs/", UA) is False
    assert robots_allows_listing("User-agent: *\nDisallow: /jobs/search", UA) is False
    assert robots_allows_listing("", UA) is True          # empty -> permissive
    assert robots_allows_listing("garbage-not-robots", UA) is True
