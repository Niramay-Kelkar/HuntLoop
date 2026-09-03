"""
Workday spider - scrapes a company's Workday-hosted careers board via the
public CXS ("candidate experience service") JSON API, the same endpoint
the rendered board itself calls.

Mechanism proven end-to-end on 5 real tenants before this was built (see
SESSIONS.md 2026-08-30 "Prove Workday {tenant, dc, site} discovery" and
"Build the Workday spider"):

  list:    POST {cxs}/jobs   body {"limit":20,"offset":N,"appliedFacets":{}}
           -> {"total": M, "jobPostings": [{title, externalPath, postedOn, ...}]}
           offset-paginated: request offsets 0, 20, 40 ... < total.

  detail:  GET  {cxs}{externalPath}
           -> {"jobPostingInfo": {jobDescription, startDate, location,
               additionalLocations, timeType, jobReqId, externalUrl, ...}}

  where {cxs} = https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}

The detail request per job is not optional - the list response carries no
description (needed for relevance/embeddings) and only a RELATIVE posted
date ("Posted 3 Days Ago"); the detail response carries the HTML
description and an absolute ``startDate``. See huntloop.workday_url.

Each company's {tenant, dc, site} triple is parsed out of its stored
``companies.careers_url`` (populated by
scripts/discover_and_store_workday.py) - see huntloop.workday_url for the
storage-decision rationale.
"""
import json
import logging
from typing import Any, Iterable

import scrapy

from .. import metrics
from ..items import JobPostingItem
from ..workday_url import build_cxs_base, normalize_workday_date, parse_workday_careers_url

logger = logging.getLogger(__name__)


class WorkdayScraper(scrapy.Spider):
    name = "workday_api"

    custom_settings = {
        "DOWNLOAD_DELAY": 0.4,
        "CONCURRENT_REQUESTS": 4,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        # Workday's CXS API 406s a request with no browser-ish headers
        # (verified against real tenants) - unlike Greenhouse/Lever, which
        # serve Scrapy's default UA fine.
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    }

    PAGE_SIZE = 20
    # Safety valve: 500 pages * 20 = 10,000 postings per company. No real
    # sponsor board is anywhere near this; a runaway `total` shouldn't
    # schedule an unbounded number of requests.
    MAX_PAGES = 500

    def __init__(self, companies=None, careers_urls=None, *args, **kwargs):
        """``careers_urls``: {company_name: careers_url}. ``companies`` is
        accepted for call-signature parity with the Greenhouse/Lever
        spiders (main.py passes it) but is only used to filter/order the
        careers_urls map. A company routed here with no usable
        careers_url is skipped with a warning, never guessed at."""
        super().__init__(*args, **kwargs)

        if isinstance(careers_urls, str):
            # CLI shape: -a careers_urls="name=url,name2=url2"
            careers_urls = dict(
                pair.split("=", 1) for pair in careers_urls.split(",") if "=" in pair
            )
        self._careers_urls = dict(careers_urls or {})

        if companies is None:
            names = list(self._careers_urls)
        elif isinstance(companies, str):
            names = [c.strip() for c in companies.split(",") if c.strip()]
        else:
            names = list(companies)
        self._company_names = names

    def _targets(self) -> Iterable[tuple[str, str, str, str]]:
        for name in self._company_names:
            url = self._careers_urls.get(name)
            if not url:
                logger.warning("Workday: no careers_url for %r - skipping (not guessing)", name)
                continue
            try:
                tenant, dc, site = parse_workday_careers_url(url)
            except ValueError as exc:
                logger.warning("Workday: bad careers_url for %r (%s) - skipping", name, exc)
                metrics.scrape_errors_total.labels(company=name, source=self.name).inc()
                continue
            yield name, tenant, dc, site

    def _jobs_request(self, cxs_base: str, company_name: str, tenant: str, offset: int) -> scrapy.Request:
        return scrapy.Request(
            url=f"{cxs_base}/jobs",
            method="POST",
            body=json.dumps({"limit": self.PAGE_SIZE, "offset": offset, "appliedFacets": {}, "searchText": ""}),
            headers={"Content-Type": "application/json"},
            callback=self.parse_list,
            meta={"company_name": company_name, "cxs_base": cxs_base, "tenant": tenant, "offset": offset},
            dont_filter=True,
        )

    def start_requests(self) -> Iterable[Any]:
        for name, tenant, dc, site in self._targets():
            cxs_base = build_cxs_base(tenant, dc, site)
            logger.info("Workday: starting %s (%s.%s/%s)", name, tenant, dc, site)
            yield self._jobs_request(cxs_base, name, tenant, 0)

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def parse_list(self, response):
        company_name = response.meta["company_name"]
        cxs_base = response.meta["cxs_base"]
        tenant = response.meta["tenant"]
        offset = response.meta["offset"]

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=company_name, source=self.name).inc()
            logger.error("Workday: non-JSON list response for %s (offset %d): %s",
                         company_name, offset, response.text[:200])
            return

        postings = data.get("jobPostings")
        if not isinstance(postings, list):
            metrics.scrape_errors_total.labels(company=company_name, source=self.name).inc()
            logger.warning("Workday: no jobPostings for %s (offset %d): %s",
                           company_name, offset, str(data)[:200])
            return

        for jp in postings:
            if not isinstance(jp, dict) or not jp.get("externalPath"):
                continue
            yield scrapy.Request(
                url=f"{cxs_base}{jp['externalPath']}",
                callback=self.parse_detail,
                meta={
                    "company_name": company_name,
                    "tenant": tenant,
                    "list_posted_on": jp.get("postedOn"),
                },
            )

        # `total` is only reliably present on the offset-0 response, so
        # fan out every remaining page from here rather than walking
        # offset-by-offset (later pages' parse_list calls just yield
        # detail requests and don't re-check total).
        if offset == 0:
            total = data.get("total")
            if isinstance(total, int) and total > self.PAGE_SIZE:
                pages = min(-(-total // self.PAGE_SIZE), self.MAX_PAGES)
                logger.info("Workday: %s has %d postings (%d pages)", company_name, total, pages)
                for page in range(1, pages):
                    yield self._jobs_request(cxs_base, company_name, tenant, page * self.PAGE_SIZE)

    def parse_detail(self, response):
        company_name = response.meta["company_name"]
        list_posted_on = response.meta.get("list_posted_on")

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=company_name, source=self.name).inc()
            logger.error("Workday: non-JSON detail response for %s: %s", company_name, response.text[:200])
            return

        jpi = data.get("jobPostingInfo")
        if not isinstance(jpi, dict):
            metrics.scrape_errors_total.labels(company=company_name, source=self.name).inc()
            logger.warning("Workday: detail response missing jobPostingInfo for %s", company_name)
            return

        job_url = jpi.get("externalUrl") or response.url
        req_id = jpi.get("jobReqId") or jpi.get("jobPostingId") or ""
        tenant = response.meta["tenant"]

        locations = []
        if jpi.get("location"):
            locations.append(jpi["location"])
        for extra in jpi.get("additionalLocations") or []:
            if extra and extra not in locations:
                locations.append(extra)

        item = JobPostingItem()
        # Namespaced so a bare Workday requisition id (e.g. "R-10063393")
        # can't false-collide with another company's in the pipeline's
        # global gh_job_id dedup check. job_url stays the canonical key.
        item["job_id"] = f"{tenant}_{req_id}" if req_id else job_url
        item["job_title"] = jpi.get("title")
        item["job_url"] = job_url
        item["job_description"] = jpi.get("jobDescription")
        item["company_name"] = company_name
        item["name"] = self.name  # source name: workday_api
        item["date_posted"] = normalize_workday_date(jpi.get("startDate"), jpi.get("postedOn") or list_posted_on)
        item["department"] = None  # Workday's CXS payload doesn't expose job family reliably
        item["employment_type"] = jpi.get("timeType")
        item["job_locations"] = locations
        item["job_skills"] = []
        item["metadata_json"] = json.dumps({
            "jobReqId": req_id,
            "timeType": jpi.get("timeType"),
            "remoteType": jpi.get("remoteType"),
            "country": (jpi.get("country") or {}).get("descriptor") if isinstance(jpi.get("country"), dict) else jpi.get("country"),
            "postedOn": jpi.get("postedOn"),
            "startDate": jpi.get("startDate"),
        })

        logger.info("Workday: yielding %r for %s", item["job_title"], company_name)
        yield item
