"""
Gem spider - scrapes a company's Gem-hosted careers board via the public
GraphQL endpoint the ``jobs.gem.com/{slug}`` React SPA itself calls.

The only per-company identifier is the ``boardId`` slug, resolved +
confidence-gated once at onboarding by
``scripts/discover_and_store_gem.py`` (which reuses
``scripts/discover_gem_job_board.py``) and stored as
``companies.ats_token``. This spider just takes those slugs.

  POST https://jobs.gem.com/api/public/graphql/batch     (JSON array body)

Confirmed live 2026-09-02 (see SESSIONS.md's Gem-discovery entries):
  * ``boardId`` == the ``jobs.gem.com/{slug}`` vanity slug, no auth/
    cookies needed.
  * ``JobBoardList($boardId)`` returns EVERY posting in one response - NO
    pagination (verified on boards of 100+ jobs) - but no description;
    each posting already carries title/locations/department/
    employmentType.
  * A SEPARATE ``ExternalJobPosting($boardId, $extId)`` query per posting
    is required for the HTML description + first-published timestamp -
    unlike Ashby, that data is genuinely not in the list response.
  * BUT the endpoint is a real GraphQL *batch* endpoint: multiple
    operations posted together in one JSON array are all executed and
    answered in one HTTP round trip, in request order (confirmed live
    with a single 109-operation array - 1 list op + 108 detail ops for a
    108-job board - one 200 response, all 108 resolved). So a whole
    company's data - the list plus every job's detail - needs only TWO
    real HTTP requests: one to learn the extIds, then one batched detail
    call carrying every ``ExternalJobPosting`` query at once (chunked at
    ``MAX_DETAIL_BATCH`` per request as a safety valve for an unusually
    large board, not because smaller batches were ever seen to fail).
  * Real response shapes: unknown slug -> ``jobBoardExternal: null``,
    ``jobPostings: []``; real board -> non-null ``jobBoardExternal`` +
    (possibly empty) ``jobPostings``.
  * ``robots.txt`` -> HTTP 404 (nothing published to obey or violate).

Graceful degradation: an unknown slug (``jobBoardExternal: null``), an
empty ``jobPostings`` array, a malformed/non-JSON response, or a mid-scrape
HTTP error on a detail-batch request are all logged and skipped with a
``scrape_errors`` metric, never a spider crash - same standard as every
prior spider in this project. Relevance classification + the resume-match
embedding are populated at insert time by the shared ``JobDataPipeline``
(needs torch -> run via the ``app`` Docker image), exactly like every
other source.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

import scrapy

from .. import metrics
from ..items import JobPostingItem

logger = logging.getLogger(__name__)

_ENDPOINT = "https://jobs.gem.com/api/public/graphql/batch"

# Same queries scripts/discover_gem_job_board.py already verified live -
# reused here unchanged in shape, just also fetching detail.
_LIST_QUERY = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings { id extId title locations { name city isoCountry isRemote } job { department { name } locationType employmentType } }
  }
  jobBoardExternal(vanityUrlPath: $boardId) { id teamDisplayName pageTitle }
}
""".strip()

_DETAIL_QUERY = """
query ExternalJobPosting($boardId: String!, $extId: String!) {
  oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
    descriptionHtml
    firstPublishedTsSec
    compensationHtml
    jobPostSectionHtml { introHtml outroHtml }
  }
}
""".strip()


class GemScraper(scrapy.Spider):
    name = "gem_api"
    allowed_domains = ["jobs.gem.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 4,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3,
        # jobs.gem.com/robots.txt is a genuine HTTP 404 - nothing
        # published to obey - and the endpoint is a public GraphQL API
        # (path literally /api/public/), not a page to crawl, the same
        # reasoning as the Ashby/SmartRecruiters carve-outs. Left as the
        # project default (True) since there's no robots.txt to conflict
        # with in the first place.
    }

    # Safety valve, mirroring SmartRecruiters' MAX_PAGES / iCIMS' MAX_PAGES:
    # no real candidate board has been anywhere near this many postings in
    # one company - one detail-batch POST per this many jobs, not because
    # a bigger single batch was ever observed to fail (109 ops in one
    # request worked fine live), just to keep any one request bounded.
    MAX_DETAIL_BATCH = 100

    DEFAULT_SLUGS: list[str] = []

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.slugs = list(self.DEFAULT_SLUGS)
        elif isinstance(companies, str):
            # Scrapy CLI (-a companies=...) always arrives as a string.
            self.slugs = [c.strip() for c in companies.split(",") if c.strip()]
        else:
            self.slugs = list(companies)

    @staticmethod
    def _graphql_request(payload: list[dict], callback, meta: dict, errback=None) -> scrapy.Request:
        return scrapy.Request(
            url=_ENDPOINT,
            method="POST",
            headers={"Content-Type": "application/json"},
            body=json.dumps(payload),
            callback=callback,
            errback=errback,
            meta=meta,
            dont_filter=True,
        )

    def start_requests(self) -> Iterable[Any]:
        for slug in self.slugs:
            logger.info("Gem: starting %s", slug)
            yield self._graphql_request(
                [{"operationName": "JobBoardList", "query": _LIST_QUERY, "variables": {"boardId": slug}}],
                callback=self.parse_list,
                errback=self.on_error,
                meta={"slug": slug},
            )

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def on_error(self, failure):
        slug = failure.request.meta.get("slug", "unknown")
        metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
        logger.warning("Gem: request failed for %s: %s", slug, failure.value)

    def parse_list(self, response):
        slug = response.meta["slug"]

        try:
            body = json.loads(response.text)
            payload = body[0]["data"]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.error("Gem: malformed list response for %s (%s): %s", slug, exc, response.text[:200])
            return

        board = payload.get("jobBoardExternal")
        if board is None:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("Gem: %s is not a real board (jobBoardExternal: null), skipping", slug)
            return

        jobs = ((payload.get("oatsExternalJobPostings") or {}).get("jobPostings")) or []
        if not jobs:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("Gem: %s is a real board with 0 listed jobs, skipping", slug)
            return

        org_name = board.get("teamDisplayName") or board.get("pageTitle")
        logger.info("Gem: %s (%s) has %d jobs", slug, org_name, len(jobs))

        jobs_by_ext_id = {j.get("extId"): j for j in jobs if isinstance(j, dict) and j.get("extId")}
        ext_ids = list(jobs_by_ext_id)
        for start in range(0, len(ext_ids), self.MAX_DETAIL_BATCH):
            chunk_ids = ext_ids[start:start + self.MAX_DETAIL_BATCH]
            detail_payload = [
                {"operationName": "ExternalJobPosting", "query": _DETAIL_QUERY,
                 "variables": {"boardId": slug, "extId": ext_id}}
                for ext_id in chunk_ids
            ]
            yield self._graphql_request(
                detail_payload,
                callback=self.parse_detail_batch,
                errback=self.on_error,
                meta={
                    "slug": slug,
                    "ext_ids": chunk_ids,
                    "jobs_by_ext_id": {eid: jobs_by_ext_id[eid] for eid in chunk_ids},
                },
            )

    def parse_detail_batch(self, response):
        slug = response.meta["slug"]
        ext_ids = response.meta["ext_ids"]
        jobs_by_ext_id = response.meta["jobs_by_ext_id"]

        try:
            body = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.error("Gem: non-JSON detail-batch response for %s: %s", slug, response.text[:200])
            return

        if not isinstance(body, list) or len(body) != len(ext_ids):
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.error(
                "Gem: detail-batch response shape mismatch for %s (expected %d ops, got %s)",
                slug, len(ext_ids), len(body) if isinstance(body, list) else type(body),
            )
            return

        for ext_id, entry in zip(ext_ids, body):
            job = jobs_by_ext_id.get(ext_id)
            detail = (entry or {}).get("data", {}).get("oatsExternalJobPosting") if isinstance(entry, dict) else None
            if job is None or detail is None:
                metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
                logger.warning("Gem: no detail data for %s extId=%s, skipping that job", slug, ext_id)
                continue
            item = self._to_item(slug, job, detail)
            if item is not None:
                logger.info("Gem: yielding %r for %s", item["job_title"], slug)
                yield item

    @staticmethod
    def _description(detail: dict) -> str | None:
        html = detail.get("descriptionHtml")
        if html:
            return html
        section = detail.get("jobPostSectionHtml") or {}
        parts = [p for p in (section.get("introHtml"), section.get("outroHtml")) if p]
        return "\n".join(parts) or None

    @staticmethod
    def _date_posted(detail: dict) -> str | None:
        ts = detail.get("firstPublishedTsSec")
        if not isinstance(ts, (int, float)):
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except (ValueError, OSError):
            return None

    @staticmethod
    def _locations(job: dict) -> list[str]:
        locs = []
        for loc in job.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            name = loc.get("name") or loc.get("city")
            if name and name not in locs:
                locs.append(name)
        return locs

    def _to_item(self, slug: str, job: dict, detail: dict) -> JobPostingItem | None:
        job_id = job.get("id")
        ext_id = job.get("extId")
        title = (job.get("title") or "").strip()
        job_url = f"https://jobs.gem.com/{slug}/{ext_id}" if ext_id else None

        if not job_id or not job_url or not title:
            metrics.scrape_errors_total.labels(company=slug, source=self.name).inc()
            logger.warning("Gem: skipping malformed job (id=%r) for %s", job_id, slug)
            return None

        item = JobPostingItem()
        # Namespaced so a bare Gem internal id can't false-collide with
        # another source's id in the pipeline's global dedup. job_url
        # stays the canonical unique key. gh_job_id is varchar(50); the
        # observed Gem "id" is ~28 chars, so "{slug}_{id}" usually fits -
        # fall back to a bare "gem_{id}" for a long slug, same pattern as
        # every other spider here.
        namespaced = f"{slug}_{job_id}"
        item["job_id"] = namespaced if len(namespaced) <= 50 else f"gem_{job_id}"
        item["job_title"] = title
        item["job_url"] = job_url
        item["job_description"] = self._description(detail)
        item["company_name"] = slug
        item["name"] = self.name  # source name: gem_api
        item["date_posted"] = self._date_posted(detail)
        job_meta = job.get("job") or {}
        item["department"] = (job_meta.get("department") or {}).get("name")
        item["employment_type"] = job_meta.get("employmentType")
        item["job_locations"] = self._locations(job)
        item["job_skills"] = []
        item["metadata_json"] = json.dumps({
            "extId": ext_id,
            "locationType": job_meta.get("locationType"),
            "hasCompensationHtml": bool(detail.get("compensationHtml")),
            "firstPublishedTsSec": detail.get("firstPublishedTsSec"),
        })
        return item
