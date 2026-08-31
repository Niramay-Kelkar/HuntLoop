"""
Run ONLY the Workday spider - for proving the spider end-to-end without
re-crawling every Greenhouse/Lever company via main.py. Same pipeline
(JobDataPipeline), same relevance classification, same metrics.

  PYTHONPATH=src python scripts/scrape_workday.py            # all workday companies
  PYTHONPATH=src python scripts/scrape_workday.py nxp cdw    # just these

Companies are read from the `companies` table (ats_platform='workday',
careers_url set). main.py routes the same rows the same way in the real
daily run - this is just a scoped entrypoint.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)
os.environ.setdefault("SCRAPY_SETTINGS_MODULE", "huntloop.settings")

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from huntloop.db_models import Company
from huntloop.settings import DATABASE_URL
from huntloop.spiders.workday_spider import WorkdayScraper


def main():
    wanted = set(sys.argv[1:])
    session = sessionmaker(bind=create_engine(DATABASE_URL))()
    rows = session.query(Company).filter(
        Company.ats_platform == "workday", Company.careers_url.isnot(None)
    ).all()
    session.close()

    careers_urls = {c.name: c.careers_url for c in rows if not wanted or c.name in wanted}
    if not careers_urls:
        print(f"No matching workday companies (wanted={wanted or 'ALL'})")
        return
    print(f"Scraping {len(careers_urls)} Workday companies: {sorted(careers_urls)}")

    process = CrawlerProcess(get_project_settings())
    process.crawl(WorkdayScraper, companies=list(careers_urls), careers_urls=careers_urls)
    process.start()


if __name__ == "__main__":
    main()
