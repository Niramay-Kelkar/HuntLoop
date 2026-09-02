import json
from typing import Iterable, Any
import logging
import scrapy
from scrapy.http import Response
from ..items import JobPostingItem
from .. import metrics

logger = logging.getLogger(__name__)


class GreenhouseScraper(scrapy.Spider):
    name = 'greenhouse_api'
    allowed_domains = ["api.greenhouse.io"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 4,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3
    }

    # Default when no `companies` argument is given (e.g.
    # `process.crawl(GreenhouseScraper)` with no kwargs) - preserves the
    # single-company behavior this spider had before it accepted a list,
    # for backward compatibility.
    DEFAULT_COMPANY_TOKENS = ['checkr']

    base_url = 'https://api.greenhouse.io/v1/boards/{company_token}/jobs?content=true'

    def __init__(self, companies=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if companies is None:
            self.company_tokens = list(self.DEFAULT_COMPANY_TOKENS)
        elif isinstance(companies, str):
            # Scrapy CLI spider arguments (-a companies=...) always arrive
            # as a string, so accept a comma-separated one.
            self.company_tokens = [token.strip() for token in companies.split(',') if token.strip()]
        else:
            # Programmatic invocation (e.g. process.crawl(GreenhouseScraper,
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

        # Validate data format
        if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], list):
            metrics.scrape_errors_total.labels(company=comp_token, source=self.name).inc()
            self.logger.warning(f"No jobs found or invalid format for {comp_token}: {type(data)}")
            return

        for job in data["jobs"]:
            if not isinstance(job, dict):
                logger.warning(f"Skipping malformed job entry for {comp_token}: {job}")
                continue
            item = JobPostingItem()
            #print(job)
            item['job_id'] = job.get('id')
            item['job_title'] = job.get('title')
            item['job_url'] = job.get('absolute_url')
            item['date_posted'] = job.get('first_published')
            item['company_name'] = comp_token
            item['name'] = self.name  # source name (e.g., greenhouse_api)
            item['job_description'] = job.get('content')

            # Locations
            locations = [office.get('name') for office in job.get('offices', []) if office.get('name')]
            primary_location = job.get('location', {}).get('name')
            if primary_location and primary_location not in locations:
                locations.append(primary_location)
            item['job_locations'] = locations

            # Empty skills list for now
            item['job_skills'] = []

            # Optional: store raw metadata
            metadata = job.get('metadata', [])
            item['metadata_json'] = json.dumps(metadata)

            # Real department field: Greenhouse jobs carry a structured
            # `departments` list (verified live - e.g. qualtrics/riotgames/
            # checkr/duolingo/figma all populate it, while `metadata`
            # entries in practice are per-company custom fields like
            # "Career Site Category" that rarely if ever contain the word
            # "department"). Use the first department's name.
            departments = job.get('departments') or []
            if departments and isinstance(departments[0], dict):
                item['department'] = departments[0].get('name')

            # Extract known fields for direct DB columns
            if metadata:
                for entry in metadata:
                    name = entry.get('name', '').lower()
                    value = entry.get('value')
                    if 'department' in name and not item.get('department'):
                        item['department'] = value
                    elif 'employment type' in name:
                        item['employment_type'] = value
                    elif 'skills' in name or 'requirements' in name:
                        item['skills_list'] = [s.strip() for s in value.split(',') if s.strip()]

            self.logger.info(f"Yielding job item: {item['job_title']} for {comp_token}")
            yield item


            # Use the token for the company name (will be confirmed in the Pipeline)
            # item['company_name'] = comp_token
            # item['source_name'] = self.name
            #
            # # The 'content' field is the raw HTML job description
            # item['job_description'] = job.get('content')
            #
            # # --- Extract Locations for job_locations table ---
            # # Greenhouse often lists multiple offices, we'll collect all of them.
            # locations = [office.get('name') for office in job.get('offices', []) if office.get('name')]
            #
            # # IMPORTANT: The single 'location' field also needs to be added to the list
            # primary_location = job.get('location', {}).get('name')
            # if primary_location and primary_location not in locations:
            #     locations.append(primary_location)
            #
            # item['locations_list'] = locations
            #
            # # --- Metadata (for later use or JSONB storage) ---
            # metadata = {
            #     'metadata': job.get('metadata', [])
            # }
            # item['metadata_json'] = json.dumps(metadata)
            # self.logger.info(f"Yielding job item: {job.get('title')}")
            # yield item