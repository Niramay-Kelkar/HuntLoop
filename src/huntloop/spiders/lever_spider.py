import json
from datetime import datetime, timezone
from typing import Iterable, Any
import logging
import scrapy
from scrapy.http import Response
from ..items import JobPostingItem
from .. import metrics

logger = logging.getLogger(__name__)


class LeverScraper(scrapy.Spider):
    name = 'lever_api'
    allowed_domains = ["api.lever.co"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 4,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3
    }

    # Default when no `companies` argument is given (e.g.
    # `process.crawl(LeverScraper)` with no kwargs) - preserves the
    # single-company behavior this spider had before it accepted a list,
    # for backward compatibility.
    DEFAULT_COMPANY_TOKENS = ['wealthfront']

    base_url = 'https://api.lever.co/v0/postings/{company_token}?mode=json'

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.company_tokens = list(self.DEFAULT_COMPANY_TOKENS)
        elif isinstance(companies, str):
            # Scrapy CLI spider arguments (-a companies=...) always arrive
            # as a string, so accept a comma-separated one.
            self.company_tokens = [token.strip() for token in companies.split(',') if token.strip()]
        else:
            # Programmatic invocation (e.g. process.crawl(LeverScraper,
            # companies=[...])) can pass a real list/tuple directly.
            self.company_tokens = list(companies)

    def start_requests(self) -> Iterable[Any]:
        for company_token in self.company_tokens:
            url = self.base_url.format(company_token=company_token)
            yield scrapy.Request(
                url=url,
                callback=self.parse,
                meta={'company_token': company_token}
            )

    async def start(self) -> Any:
        for request in self.start_requests():
            yield request

    def parse(self, response):
        comp_token = response.meta['company_token']

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            metrics.scrape_errors_total.labels(company=comp_token, source=self.name).inc()
            self.logger.error(f"Non-JSON response for company {comp_token}: {response.text[:200]}")
            return

        # Lever's postings endpoint returns a bare JSON array, not an
        # {"jobs": [...]} envelope like Greenhouse's.
        if not isinstance(data, list):
            metrics.scrape_errors_total.labels(company=comp_token, source=self.name).inc()
            self.logger.warning(f"No jobs found or invalid format for {comp_token}: {type(data)}")
            return

        for job in data:
            if not isinstance(job, dict):
                logger.warning(f"Skipping malformed job entry for {comp_token}: {job}")
                continue
            item = JobPostingItem()
            item['job_id'] = job.get('id')
            item['job_title'] = job.get('text')
            item['job_url'] = job.get('hostedUrl')
            item['job_description'] = job.get('description')
            item['company_name'] = comp_token
            item['name'] = self.name  # source name (lever_api)

            # createdAt is epoch milliseconds, not an ISO8601 string like
            # Greenhouse's first_published - convert so it lands in the
            # DateTime column the same way.
            created_at_ms = job.get('createdAt')
            if isinstance(created_at_ms, (int, float)):
                item['date_posted'] = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc).isoformat()
            else:
                item['date_posted'] = None

            categories = job.get('categories') or {}
            item['department'] = categories.get('department')
            item['employment_type'] = categories.get('commitment')

            # Locations
            locations = list(categories.get('allLocations') or [])
            primary_location = categories.get('location')
            if primary_location and primary_location not in locations:
                locations.append(primary_location)
            item['job_locations'] = locations

            # Empty skills list for now
            item['job_skills'] = []

            # Store raw categories/workplace metadata for later use
            metadata = {
                'categories': categories,
                'workplaceType': job.get('workplaceType'),
                'country': job.get('country'),
                'tags': job.get('tags'),
            }
            item['metadata_json'] = json.dumps(metadata)

            self.logger.info(f"Yielding job item: {item['job_title']} for {comp_token}")
            yield item
