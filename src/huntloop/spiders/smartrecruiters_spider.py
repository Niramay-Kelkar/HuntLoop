"""
SmartRecruiters spider - scrapes a company's SmartRecruiters-hosted
careers board via the public Posting API, the same data the rendered
``jobs.smartrecruiters.com/{companyId}`` board is built from.

Unlike Workday, there is NO per-tenant discovery complexity at scrape
time: the only per-company identifier is the ``companyId`` string, which
is resolved + confidence-gated once at onboarding by
``scripts/discover_and_store_smartrecruiters.py`` (which reuses
``scripts/discover_smartrecruiters_id.py``) and stored as
``companies.ats_token``. This spider just takes those ids.

  list:    GET {API}/companies/{companyId}/postings?limit=100&offset=N
           -> {"totalFound": M, "content": [{id, name, refNumber,
               releasedDate, location, typeOfEmployment, department, ...}]}
           offset-paginated: request offsets 0, 100, 200 ... < totalFound.
           An unknown/invalid companyId returns HTTP 200 with
           totalFound: 0 (never a 404).

  detail:  GET {API}/companies/{companyId}/postings/{postingId}
           -> {..., "jobAd": {"sections": {"jobDescription": {"text": ...},
               "qualifications": {"text": ...}, ...}}, "postingUrl": ...}

The detail request per posting is not optional - the list response
carries no job description (needed for the pipeline's relevance
classification + resume-match embedding), only structured metadata.

  where {API} = https://api.smartrecruiters.com/v1

Graceful degradation (task requirement): a board that returns
``totalFound: 0`` or errors is skipped with a logged reason and a
scrape-error metric, never a spider crash. A board that resolves and
returns real postings is scraped as-is even if the postings look old /
inactive - deciding "is this board still active" is a downstream
relevance/staleness concern, not this spider's call.
"""
import json
import logging
from typing import Any, Iterable

import scrapy

from .. import metrics
from ..company_display_name import pick_smartrecruiters_display_name
from ..items import JobPostingItem

logger = logging.getLogger(__name__)

_API = "https://api.smartrecruiters.com/v1"


class SmartRecruitersScraper(scrapy.Spider):
    name = "smartrecruiters_api"
    allowed_domains = ["api.smartrecruiters.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.3,
        "CONCURRENT_REQUESTS": 6,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        # api.smartrecruiters.com/robots.txt is `Disallow: /` for `*`
        # (with an explicit `Allow: /v1/companies/` carve-out, but scoped
        # to LinkedInBot). The Posting API we hit here IS SmartRecruiters'
        # documented public read API (developers.smartrecruiters.com) -
        # the same feed LinkedIn / Indeed / Google Jobs consume - not a
        # site to crawl. Treat this spider as an API client, like the
        # Greenhouse/Lever/Workday spiders are for their vendors' APIs;
        # the project-wide ROBOTSTXT_OBEY=True stays on for everything else.
        "ROBOTSTXT_OBEY": False,
    }

    # SmartRecruiters caps a page at 100 (limit=200 still returns 100).
    PAGE_SIZE = 100
    # Safety valve: 200 pages * 100 = 20,000 postings/company. No real
    # sponsor board is near this; a runaway totalFound shouldn't schedule
    # an unbounded number of requests.
    MAX_PAGES = 200

    # Default when no `companies` argument is given (parity with the other
    # spiders' DEFAULT_* handling - keeps `process.crawl(SmartRecruitersScraper)`
    # from erroring, though in practice main.py always passes the list).
    DEFAULT_COMPANY_IDS: list[str] = []

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.company_ids = list(self.DEFAULT_COMPANY_IDS)
        elif isinstance(companies, str):
            # Scrapy CLI (-a companies=...) always arrives as a string.
            self.company_ids = [c.strip() for c in companies.split(",") if c.strip()]
        else:
            self.company_ids = list(companies)

    def _postings_url(self, company_id: str, offset: int) -> str:
        return f"{_API}/companies/{company_id}/postings?limit={self.PAGE_SIZE}&offset={offset}"

    def _list_request(self, company_id: str, offset: int) -> scrapy.Request:
        return scrapy.Request(
            url=self._postings_url(company_id, offset),
            callback=self.parse_list,
            meta={"company_id": company_id, "offset": offset},
            dont_filter=True,
        )

    def start_requests(self) -> Iterable[Any]:
        for company_id in self.company_ids:
            logger.info("SmartRecruiters: starting %s", company_id)
            yield self._list_request(company_id, 0)

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def parse_list(self, response):
        company_id = response.meta["company_id"]
        offset = response.meta["offset"]

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=company_id, source=self.name).inc()
            logger.error("SmartRecruiters: non-JSON list response for %s (offset %d): %s",
                         company_id, offset, response.text[:200])
            return

        content = data.get("content")
        if not isinstance(content, list):
            metrics.scrape_errors_total.labels(company=company_id, source=self.name).inc()
            logger.warning("SmartRecruiters: no content array for %s (offset %d): %s",
                           company_id, offset, str(data)[:200])
            return

        total = data.get("totalFound")
        if offset == 0 and (not isinstance(total, int) or total == 0):
            # Board resolved (200) but is empty - stale/deregistered id, or
            # a company that offboarded. Skip with a reason, not a crash.
            logger.warning(
                "SmartRecruiters: %s returned totalFound=%r - board is empty, skipping",
                company_id, total,
            )
            metrics.scrape_errors_total.labels(company=company_id, source=self.name).inc()
            return

        for posting in content:
            if not isinstance(posting, dict) or not posting.get("id"):
                continue
            posting_id = posting["id"]
            yield scrapy.Request(
                url=f"{_API}/companies/{company_id}/postings/{posting_id}",
                callback=self.parse_detail,
                meta={
                    "company_id": company_id,
                    "list_posting": posting,
                },
            )

        # totalFound is on every page, but fan out remaining pages only
        # from offset 0 (later pages just yield detail requests).
        if offset == 0 and isinstance(total, int) and total > self.PAGE_SIZE:
            pages = min(-(-total // self.PAGE_SIZE), self.MAX_PAGES)
            logger.info("SmartRecruiters: %s has %d postings (%d pages)", company_id, total, pages)
            for page in range(1, pages):
                yield self._list_request(company_id, page * self.PAGE_SIZE)

    @staticmethod
    def _description(detail: dict) -> str | None:
        """Join the human-written sections of the job ad into one HTML
        blob for the pipeline to clean + embed. companyDescription is
        left out - it's boilerplate that's identical across every posting
        on the board and would wash out the embedding signal."""
        sections = ((detail.get("jobAd") or {}).get("sections")) or {}
        parts = []
        for key in ("jobDescription", "qualifications", "additionalInformation"):
            text = (sections.get(key) or {}).get("text")
            if text:
                parts.append(text)
        return "\n".join(parts) or None

    def parse_detail(self, response):
        company_id = response.meta["company_id"]
        list_posting = response.meta.get("list_posting") or {}

        try:
            detail = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=company_id, source=self.name).inc()
            logger.error("SmartRecruiters: non-JSON detail response for %s: %s",
                         company_id, response.text[:200])
            return

        posting_id = detail.get("id") or list_posting.get("id")
        if not posting_id:
            metrics.scrape_errors_total.labels(company=company_id, source=self.name).inc()
            logger.warning("SmartRecruiters: detail response missing id for %s", company_id)
            return

        loc = detail.get("location") or list_posting.get("location") or {}
        location_str = loc.get("fullLocation") or ", ".join(
            p for p in (loc.get("city"), loc.get("region"), loc.get("country")) if p
        )

        emp = detail.get("typeOfEmployment") or list_posting.get("typeOfEmployment") or {}
        dept = detail.get("department") or list_posting.get("department") or {}

        item = JobPostingItem()
        # Namespaced so a bare SmartRecruiters posting id can't false-
        # collide with another source's id in the pipeline's global dedup.
        # job_url stays the canonical unique key. gh_job_id is varchar(50),
        # so for a long companyId (e.g.
        # "internationalqualityhomecarecorporation_...") fall back to an
        # "sr_" prefix - SmartRecruiters posting ids are globally unique,
        # so it still can't collide with another source.
        namespaced = f"{company_id}_{posting_id}"
        item["job_id"] = namespaced if len(namespaced) <= 50 else f"sr_{posting_id}"
        item["job_title"] = detail.get("name") or list_posting.get("name")
        item["job_url"] = detail.get("postingUrl") or list_posting.get("postingUrl") or response.url
        item["job_description"] = self._description(detail)
        item["company_name"] = company_id
        company = detail.get("company") or list_posting.get("company") or {}
        item["company_display_name"] = pick_smartrecruiters_display_name(
            company_id, company.get("identifier"), company.get("name")
        )
        item["name"] = self.name  # source name: smartrecruiters_api
        item["date_posted"] = detail.get("releasedDate") or list_posting.get("releasedDate")
        item["department"] = dept.get("label")
        item["employment_type"] = emp.get("label")
        item["job_locations"] = [location_str] if location_str else []
        item["job_skills"] = []
        item["metadata_json"] = json.dumps({
            "refNumber": detail.get("refNumber") or list_posting.get("refNumber"),
            "postingId": posting_id,
            "function": (detail.get("function") or {}).get("label"),
            "experienceLevel": (detail.get("experienceLevel") or {}).get("label"),
            "industry": (detail.get("industry") or {}).get("label"),
            "remote": loc.get("remote"),
            "hybrid": loc.get("hybrid"),
            "releasedDate": detail.get("releasedDate"),
        })

        logger.info("SmartRecruiters: yielding %r for %s", item["job_title"], company_id)
        yield item
