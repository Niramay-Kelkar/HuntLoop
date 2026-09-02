"""
iCIMS spider - parses a company's OWN public iCIMS career portal HTML.

Unlike every other spider in this project, iCIMS has no usable public or
third-party API: its documented Job Portal API is HTTP-Basic-auth-gated
(verified - see SESSIONS.md "iCIMS discovery pass"). The legitimate
surface is the tenant's server-rendered results page at

    https://careers-{slug}.icims.com/jobs/search?pr={0-indexed page}&in_iframe=1

and one detail page per job (for the schema.org JSON-LD ``JobPosting``
block). Because this is HTML-scraping a human-facing site rather than an
intended feed, the spider is deliberately conservative:

  * ROBOTSTXT_OBEY stays ON (project default). On top of that the spider
    fetches each tenant's robots.txt itself FIRST and skips the whole
    tenant - with a logged reason - if it disallows ``/jobs/search`` (or
    is ``Disallow: /``). A disallow is never bypassed.
  * Honest identifying User-Agent (never a browser impersonation),
    ``DOWNLOAD_DELAY`` of 2s, one request at a time per host.
  * JSON-LD is the primary field source; visible-HTML parsing is a
    per-field fallback and every use is logged, so its real frequency is
    visible in the run summary (``closed()``).
  * Zero openings, a missing/broken JSON-LD block, an HTTP error mid
    scrape, or a robots-disallow discovered only after the check are all
    logged and skipped, never a crash.

Per-company identifier: just the ``careers-{slug}`` subdomain, stored as
``companies.ats_token`` (== ``companies.name``); ``careers_url`` =
``https://careers-{slug}.icims.com`` for parity/traceability. Resolved +
gated once at onboarding by ``scripts/discover_and_store_icims.py``.
"""
import json
import logging
from collections import Counter
from typing import Any, Iterable

import scrapy

from .. import metrics
from ..icims_portal import (
    careers_base_url,
    extract_jobposting_jsonld,
    find_next_page_url,
    html_description,
    html_locations,
    html_title,
    jobposting_fields,
    listing_has_no_results,
    listing_url,
    parse_listing_anchors,
    parse_page_indicator,
    robots_allows_listing,
    with_in_iframe,
)
from ..items import JobPostingItem

logger = logging.getLogger(__name__)

# Honest, identifying, non-browser. Matches what the discovery pass
# validated as safe against real iCIMS tenants.
USER_AGENT = "HuntLoop/1.0 (sponsorship-aware job aggregator; sponsorship-matching research)"


class IcimsScraper(scrapy.Spider):
    name = "icims_portal"

    custom_settings = {
        "USER_AGENT": USER_AGENT,
        "ROBOTSTXT_OBEY": True,  # project default, made explicit - never bypassed
        "DOWNLOAD_DELAY": 2.0,
        "CONCURRENT_REQUESTS": 4,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "AUTOTHROTTLE_ENABLED": True,
        "AUTOTHROTTLE_START_DELAY": 2.0,
        "AUTOTHROTTLE_MAX_DELAY": 30.0,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 2,
        "DEFAULT_REQUEST_HEADERS": {"Accept": "text/html,application/xhtml+xml"},
    }

    # Safety valve - no real DOL-sponsor iCIMS board is near this. Stops a
    # broken "Page 1 of 99999" or a next-link cycle scheduling forever.
    MAX_PAGES = 60

    DEFAULT_SLUGS: list[str] = []

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.slugs = list(self.DEFAULT_SLUGS)
        elif isinstance(companies, str):
            self.slugs = [c.strip() for c in companies.split(",") if c.strip()]
        else:
            self.slugs = list(companies)
        self._jsonld_ok = 0
        self._html_fallback_jobs = 0
        self._field_fallbacks: Counter = Counter()
        self._yielded = 0

    # --- start: robots.txt gate per tenant ---------------------------

    def start_requests(self) -> Iterable[Any]:
        for slug in self.slugs:
            logger.info("iCIMS: %s - checking robots.txt first", slug)
            yield scrapy.Request(
                url=f"{careers_base_url(slug)}/robots.txt",
                callback=self.parse_robots,
                errback=self.on_robots_error,
                meta={"slug": slug, "handle_httpstatus_all": True},
                dont_filter=True,
            )

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def on_robots_error(self, failure):
        slug = failure.request.meta.get("slug", "unknown")
        # DNS/connection failure on the robots host almost always means
        # the careers-{slug} subdomain doesn't exist. Skip, don't guess.
        metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
        logger.warning("iCIMS: %s - could not reach robots.txt (%s); skipping tenant", slug, failure.value)

    def parse_robots(self, response):
        slug = response.meta["slug"]
        robots_txt = response.text if response.status == 200 else ""
        if not robots_allows_listing(robots_txt, USER_AGENT):
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning(
                "iCIMS: %s - robots.txt disallows /jobs/search for our UA; skipping tenant entirely",
                slug,
            )
            return
        logger.info("iCIMS: %s - robots.txt permits /jobs/search; starting listing", slug)
        yield self._listing_request(slug, page=0)

    # --- listing pagination ----------------------------------------

    def _listing_request(self, slug: str, page: int) -> scrapy.Request:
        return scrapy.Request(
            url=listing_url(slug, page),
            callback=self.parse_listing,
            errback=self.on_error,
            meta={"slug": slug, "page": page},
            dont_filter=True,
        )

    def parse_listing(self, response):
        slug = response.meta["slug"]
        page = response.meta["page"]

        if response.status != 200:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("iCIMS: %s listing page %d -> HTTP %d; stopping this tenant",
                           slug, page, response.status)
            return

        html = response.text
        if listing_has_no_results(html):
            logger.info("iCIMS: %s - portal reports no open jobs (page %d); nothing to scrape", slug, page)
            return

        anchors = parse_listing_anchors(html)
        indicator = parse_page_indicator(html)
        logger.info("iCIMS: %s listing page %d - %d job anchors%s",
                    slug, page, len(anchors),
                    f" ({indicator[0]}/{indicator[1]})" if indicator else "")

        if not anchors and page == 0:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("iCIMS: %s - listing page 0 had 0 parseable job anchors; skipping tenant", slug)
            return

        for a in anchors:
            yield scrapy.Request(
                url=with_in_iframe(a["url"]),
                callback=self.parse_job,
                errback=self.on_error,
                meta={"slug": slug, "job_id": a["job_id"],
                      "canonical_url": a["url"], "title_hint": a["title_hint"]},
                dont_filter=True,
            )

        # Pagination: trust <link rel="next">, bounded by the "Page N of M"
        # indicator and MAX_PAGES.
        next_url = find_next_page_url(html, response.url)
        if not next_url:
            return
        if indicator and indicator[0] >= indicator[1]:
            return
        if page + 1 >= self.MAX_PAGES:
            logger.warning("iCIMS: %s - hit MAX_PAGES (%d); stopping pagination", slug, self.MAX_PAGES)
            return
        yield self._listing_request(slug, page + 1)

    # --- job detail: JSON-LD primary, HTML per-field fallback ------

    def parse_job(self, response):
        slug = response.meta["slug"]
        job_id = response.meta["job_id"]
        canonical_url = response.meta["canonical_url"]
        title_hint = response.meta.get("title_hint")

        if response.status != 200:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("iCIMS: %s job %s -> HTTP %d; skipping this job", slug, job_id, response.status)
            return

        html = response.text
        node = extract_jobposting_jsonld(html)

        if node is not None:
            fields = jobposting_fields(node)
            self._jsonld_ok += 1
            primary = "json-ld"
        else:
            fields = {
                "title": None, "description": None, "date_posted": None,
                "employment_type": None, "department": None, "locations": [],
                "url": None, "identifier": None, "hiring_organization": None,
                "valid_through": None,
            }
            self._html_fallback_jobs += 1
            primary = "html-fallback"
            logger.warning(
                "iCIMS: %s job %s - no JSON-LD JobPosting block; falling back to HTML parsing",
                slug, job_id,
            )

        # Per-field HTML fallback for anything genuinely missing.
        if not fields["title"]:
            from_html = html_title(html)
            fields["title"] = from_html or title_hint
            self._field_fallbacks["title"] += 1
            logger.info("iCIMS: %s job %s - title from %s", slug, job_id,
                        "HTML" if from_html else "listing anchor")
        if not fields["description"]:
            fields["description"] = html_description(html)
            if fields["description"]:
                self._field_fallbacks["description"] += 1
                logger.info("iCIMS: %s job %s - description from HTML fallback", slug, job_id)
        if not fields["locations"]:
            fields["locations"] = html_locations(html)
            if fields["locations"]:
                self._field_fallbacks["locations"] += 1
                logger.info("iCIMS: %s job %s - locations from HTML fallback", slug, job_id)
        if not fields["date_posted"]:
            # classic iCIMS exposes no reliable absolute date outside
            # JSON-LD - leave it NULL rather than invent one.
            self._field_fallbacks["date_posted_missing"] += 1

        item = self._to_item(slug, job_id, canonical_url, fields, primary)
        if item is None:
            return
        self._yielded += 1
        logger.info("iCIMS: yielding %r for %s (%s)", item["job_title"], slug, primary)
        yield item

    def _to_item(self, slug, job_id, canonical_url, fields, primary) -> JobPostingItem | None:
        namespaced = f"{slug}_{job_id}"
        job_url = fields.get("url") or canonical_url
        title = (fields.get("title") or "").strip()
        if not title or not job_url:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("iCIMS: %s job %s - no usable title/url after fallback; dropping", slug, job_id)
            return None

        item = JobPostingItem()
        item["job_id"] = namespaced if len(namespaced) <= 50 else f"icims_{job_id}"
        item["job_title"] = title
        item["job_url"] = job_url
        item["job_description"] = fields.get("description")
        item["company_name"] = slug
        item["name"] = self.name  # source: icims_portal
        item["date_posted"] = fields.get("date_posted")
        item["department"] = fields.get("department")
        item["employment_type"] = fields.get("employment_type")
        item["job_locations"] = [loc for loc in (fields.get("locations") or []) if loc][:10]
        item["job_skills"] = []
        item["metadata_json"] = json.dumps({
            "portalJobId": job_id,
            "fieldSource": primary,
            "hiringOrganization": fields.get("hiring_organization"),
            "validThrough": fields.get("valid_through"),
            "identifier": fields.get("identifier"),
        })
        return item

    # --- error handling + run summary ----------------------------

    def on_error(self, failure):
        meta = getattr(failure.request, "meta", {}) or {}
        slug = meta.get("slug", "unknown")
        job_id = meta.get("job_id")
        metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
        if job_id:
            logger.warning("iCIMS: %s job %s - request failed (%s); skipping job", slug, job_id, failure.value)
        else:
            logger.warning("iCIMS: %s - listing request failed (%s); skipping tenant", slug, failure.value)

    def closed(self, reason):
        logger.info(
            "iCIMS run done (%s): %d postings yielded | JSON-LD primary for %d, "
            "whole-job HTML fallback for %d | per-field fallbacks: %s",
            reason, self._yielded, self._jsonld_ok, self._html_fallback_jobs,
            dict(self._field_fallbacks) or "none",
        )
