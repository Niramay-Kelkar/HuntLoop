import json
from typing import Iterable, Any
import logging
import scrapy
from scrapy.http import Response
from ..items import JobPostingItem

class GreenhouseScraper(scrapy.Spider):
    name = 'greenhouse_api'
    allowed_domains = ["api.greenhouse.io"]

    custom_settings = {
        "DOWNLOAD_DELAY": 0.5,
        "CONCURRENT_REQUESTS": 4,
        "RETRY_ENABLED": True,
        "RETRY_TIMES": 3
    }

    company_tokens = [
        'checkr'
    ]
    # company_tokens = [
    #     'duolingo', 'nextdoor', 'checkr', 'paveakatroveinformationtechnologies'
    # ]

    base_url = 'https://api.greenhouse.io/v1/boards/{company_token}/jobs?content=true'

    def start_requests(self) -> Iterable[Any]:
        for company_token in self.company_tokens:
            url = self.base_url.format(company_token=company_token)
            yield scrapy.Request(
                url=url,
                callback=self.parse,
                meta={'company_token': company_token}
            )

    def parse(self, response):
        comp_token = response.meta['company_token']

        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error(f"Non-JSON response for company {comp_token}: {response.text[:200]}")
            return

        # Validate data format
        if not isinstance(data, dict) or "jobs" not in data or not isinstance(data["jobs"], list):
            self.logger.warning(f"No jobs found or invalid format for {company_token}: {type(data)}")
            return

        for job in data["jobs"]:
            if not isinstance(job, dict):
                logging.warning(f"Skipping malformed job entry for {company_token}: {job}")
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

            # Extract known fields for direct DB columns
            if metadata:
                for entry in metadata:
                    name = entry.get('name', '').lower()
                    value = entry.get('value')
                    if 'department' in name:
                        item['department'] = value
                    elif 'employment type' in name:
                        item['employment_type'] = value
                    elif 'skills' in name or 'requirements' in name:
                        item['skills_list'] = [s.strip() for s in value.split(',') if s.strip()]

            self.logger.info(f"🧩 Yielding job item: {item['job_title']} for {comp_token}")
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