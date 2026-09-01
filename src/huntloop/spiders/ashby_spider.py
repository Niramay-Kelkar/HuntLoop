"""
Ashby spider - scrapes a company's Ashby-hosted careers board via the
public Job Board API, the same data the rendered
``jobs.ashbyhq.com/{jobBoardName}`` board is built from.

The only per-company identifier is the ``jobBoardName`` slug, resolved +
confidence-gated once at onboarding by
``scripts/discover_and_store_ashby.py`` (which reuses
``scripts/discover_ashby_job_board.py``) and stored as
``companies.ats_token``. This spider just takes those slugs.

  GET {API}/posting-api/job-board/{jobBoardName}
      -> {"jobs": [ {id, title, department, team, employmentType,
                     location, secondaryLocations, publishedAt, isListed,
                     isRemote, workplaceType, jobUrl, applyUrl,
                     descriptionHtml, descriptionPlain}, ... ],
          "apiVersion": ...}

  where {API} = https://api.ashbyhq.com

Confirmed live 2026-09-01 (see SESSIONS.md "Prove Ashby job-board
discovery"):
  * NO pagination - one response returns every listed job (verified on
    boards of 700+ jobs; only keys are ``jobs`` / ``apiVersion``).
  * Each job already carries ``descriptionHtml`` and ``jobUrl`` - so,
    unlike Workday / SmartRecruiters, there is NO per-job detail fetch.
  * An unknown jobBoardName returns HTTP 404 ("Not Found"); a real board
    with nothing listed returns HTTP 200 with ``jobs: []``.

Graceful degradation: a 404 or an empty ``jobs`` array is skipped with a
logged reason and a ``scrape_errors`` metric, never a spider crash. A
board that returns real postings is scraped as-is even if some look old -
staleness is a downstream relevance concern, not this spider's call.
Relevance classification + the resume-match embedding are populated at
insert time by the shared ``JobDataPipeline`` (needs torch -> run via the
``app`` Docker image), exactly like every other source.
"""
import json
import logging
from typing import Any, Iterable

import scrapy

from .. import metrics
from ..items import JobPostingItem

logger = logging.getLogger(__name__)

_API = "https://api.ashbyhq.com/posting-api/job-board"


class AshbyScraper(scrapy.Spider):
    name = "ashby_api"
    allowed_domains = ["api.ashbyhq.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.3,
        "CONCURRENT_REQUESTS": 6,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        # The Job Board API (posting-api.ashbyhq.com docs) is Ashby's
        # documented public read feed - the same one job aggregators
        # consume. api.ashbyhq.com/robots.txt disallows crawlers, but this
        # is an API client, like the Greenhouse/Lever/Workday/SmartRecruiters
        # spiders are for their vendors' APIs; the project-wide
        # ROBOTSTXT_OBEY=True stays on for everything else.
        "ROBOTSTXT_OBEY": False,
    }

    # Default when no `companies` argument is given (parity with the other
    # spiders - keeps `process.crawl(AshbyScraper)` from erroring, though
    # main.py always passes the list).
    DEFAULT_JOB_BOARD_NAMES: list[str] = []

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.job_board_names = list(self.DEFAULT_JOB_BOARD_NAMES)
        elif isinstance(companies, str):
            # Scrapy CLI (-a companies=...) always arrives as a string.
            self.job_board_names = [c.strip() for c in companies.split(",") if c.strip()]
        else:
            self.job_board_names = list(companies)

    def start_requests(self) -> Iterable[Any]:
        for slug in self.job_board_names:
            logger.info("Ashby: starting %s", slug)
            yield scrapy.Request(
                url=f"{_API}/{slug}",
                callback=self.parse_board,
                errback=self.on_error,
                meta={"slug": slug},
                dont_filter=True,
            )

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def on_error(self, failure):
        slug = failure.request.meta.get("slug", "unknown")
        # A 404 (unknown jobBoardName) arrives here as an HttpError.
        metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
        logger.warning("Ashby: request failed for %s: %s", slug, failure.value)

    def parse_board(self, response):
        slug = response.meta["slug"]

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.error("Ashby: non-JSON response for %s: %s", slug, response.text[:200])
            return

        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning(
                "Ashby: %s returned %d jobs - board empty/deregistered, skipping",
                slug, len(jobs) if isinstance(jobs, list) else -1,
            )
            return

        logger.info("Ashby: %s has %d jobs", slug, len(jobs))
        for job in jobs:
            if not isinstance(job, dict) or not job.get("id"):
                continue
            item = self._to_item(slug, job)
            if item is not None:
                logger.info("Ashby: yielding %r for %s", item["job_title"], slug)
                yield item

    @staticmethod
    def _locations(job: dict) -> list[str]:
        locs = []
        primary = job.get("location")
        if primary:
            locs.append(primary)
        for sec in job.get("secondaryLocations") or []:
            name = sec.get("location") if isinstance(sec, dict) else None
            if name and name not in locs:
                locs.append(name)
        return locs

    def _to_item(self, slug: str, job: dict) -> JobPostingItem | None:
        job_id = job["id"]
        item = JobPostingItem()
        # Namespaced so a bare Ashby posting id (a UUID) can't false-collide
        # with another source's id in the pipeline's global dedup. job_url
        # stays the canonical unique key. gh_job_id is varchar(50); an
        # Ashby UUID is 36 chars, so "{slug}_{uuid}" can overflow - fall
        # back to a bare "ashby_{uuid}" (still globally unique).
        namespaced = f"{slug}_{job_id}"
        item["job_id"] = namespaced if len(namespaced) <= 50 else f"ashby_{job_id}"
        item["job_title"] = (job.get("title") or "").strip()
        item["job_url"] = job.get("jobUrl") or job.get("applyUrl")
        item["job_description"] = job.get("descriptionHtml") or job.get("descriptionPlain")
        item["company_name"] = slug
        item["name"] = self.name  # source name: ashby_api
        item["date_posted"] = job.get("publishedAt")
        item["department"] = job.get("department") or job.get("team")
        item["employment_type"] = job.get("employmentType")
        item["job_locations"] = self._locations(job)
        item["job_skills"] = []
        item["metadata_json"] = json.dumps({
            "postingId": job_id,
            "team": job.get("team"),
            "isRemote": job.get("isRemote"),
            "workplaceType": job.get("workplaceType"),
            "isListed": job.get("isListed"),
            "applyUrl": job.get("applyUrl"),
            "publishedAt": job.get("publishedAt"),
        })
        if not item["job_url"] or not item["job_title"]:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("Ashby: skipping malformed job %s for %s", job_id, slug)
            return None
        return item
