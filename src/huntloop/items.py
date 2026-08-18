# Define here the models for your scraped items
#
# See documentation in:
# https://docs.scrapy.org/en/latest/topics/items.html

import scrapy


class JobPostingItem(scrapy.Item):
    # define the fields for your item here like:
    # name = scrapy.Field()
    # Data for job_postings table
    job_id = scrapy.Field()
    job_title = scrapy.Field()
    job_url = scrapy.Field()
    job_description = scrapy.Field()
    gh_job_id = scrapy.Field

    # These will be mapped to IDs in the pipeline
    name = scrapy.Field()
    company_id = scrapy.Field()

    department = scrapy.Field()
    date_posted = scrapy.Field()
    employment_type = scrapy.Field()

    # Data for linked tables
    # These will be extracted as lists for insertion into separate tables
    job_locations = scrapy.Field()
    job_skills = scrapy.Field()

    # Data for 'companies' Table
    # We only need the name to check/insert the company
    company_name = scrapy.Field()

    metadata_json = scrapy.Field()

